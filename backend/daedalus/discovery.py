"""Walk the workspaces root for git repos and surface metadata.

Used by the `Discover repos` flow on the project list page so the operator
can register multiple existing git repos as Daedalus projects in one go,
without typing each path by hand.

A "git repo" is any directory directly containing a `.git` entry (file or
directory — the latter is the working-tree case, the former is the
worktree case). We never recurse into the contents of a discovered repo,
so submodules don't show up as separate candidates.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()


# Directories we never walk into. Git internals + common build outputs.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".pytest_cache",
    ".cache",
    "target",  # rust / java
}

# Hard cap on how deep we recurse from the configured root.
_MAX_DEPTH = 3

# Concurrent git subprocesses — keep low so we don't fork-bomb on big trees.
_GIT_CONCURRENCY = 8

# Per-git-call wall-clock budget.
_GIT_TIMEOUT = 5.0


@dataclass
class DiscoveredRepo:
    name: str
    path: str            # absolute path inside the container
    relative_path: str   # relative to the workspaces root, for display
    default_branch: str
    description: str
    last_commit_at: datetime | None
    has_uncommitted: bool
    already_registered: bool = False


async def discover(root: str, *, max_depth: int = _MAX_DEPTH) -> list[DiscoveredRepo]:
    """Return every git repo found at most `max_depth` levels under `root`.

    The result is sorted by name. Repos that error out during inspection
    (permission denied, broken git metadata, etc.) are silently skipped —
    the discovery flow is best-effort UX, not an audit tool.
    """
    root_p = Path(root).resolve()
    if not root_p.exists() or not root_p.is_dir():
        log.info("discovery.no_root", root=str(root_p))
        return []

    candidates: list[Path] = []
    _walk(root_p, root_p, max_depth, candidates)

    sem = asyncio.Semaphore(_GIT_CONCURRENCY)

    async def _bounded(p: Path) -> DiscoveredRepo | None:
        async with sem:
            try:
                return await _inspect(p, root_p)
            except Exception:
                log.warning("discovery.inspect_failed", path=str(p), exc_info=True)
                return None

    results = await asyncio.gather(*(_bounded(c) for c in candidates))
    return sorted(
        (r for r in results if r is not None),
        key=lambda r: r.relative_path.lower(),
    )


# ── walking ──────────────────────────────────────────────────────────────


def _walk(node: Path, root: Path, max_depth: int, out: list[Path]) -> None:
    rel_depth = 0 if node == root else len(node.relative_to(root).parts)
    if rel_depth > max_depth:
        return
    if not node.is_dir():
        return
    if (node / ".git").exists():
        out.append(node)
        return  # don't recurse into a repo's children
    try:
        entries = list(node.iterdir())
    except (PermissionError, OSError):
        return
    for entry in entries:
        if entry.name in _SKIP_DIRS:
            continue
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            # Avoid symlink loops; skip to keep the walk deterministic.
            continue
        if entry.is_dir():
            _walk(entry, root, max_depth, out)


# ── inspection ───────────────────────────────────────────────────────────


async def _inspect(repo_path: Path, root: Path) -> DiscoveredRepo | None:
    try:
        rel = str(repo_path.relative_to(root))
    except ValueError:
        return None

    default_branch = await _read_default_branch(repo_path)
    description = _read_readme(repo_path)
    last_commit_at = await _last_commit(repo_path)
    has_uncommitted = await _has_uncommitted(repo_path)

    return DiscoveredRepo(
        name=repo_path.name,
        path=str(repo_path),
        relative_path=rel,
        default_branch=default_branch,
        description=description,
        last_commit_at=last_commit_at,
        has_uncommitted=has_uncommitted,
    )


async def _git(repo: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, ""
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode(errors="replace"),
    )


async def _read_default_branch(repo: Path) -> str:
    code, out = await _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if code == 0 and out.strip().startswith("origin/"):
        return out.strip()[len("origin/"):]
    code, out = await _git(repo, "branch", "--show-current")
    if code == 0 and out.strip():
        return out.strip()
    return "main"


def _read_readme(repo: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        path = repo / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            return stripped[:240]
    return ""


async def _last_commit(repo: Path) -> datetime | None:
    code, out = await _git(repo, "log", "-1", "--format=%cI")
    if code != 0:
        return None
    raw = out.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except ValueError:
        return None


async def _has_uncommitted(repo: Path) -> bool:
    code, out = await _git(repo, "status", "--porcelain")
    return code == 0 and bool(out.strip())
