"""Pre-trust a workdir in claude's ~/.claude.json so the interactive trust
dialog is skipped on first launch.

Claude Code shows a "Quick safety check: trust this folder?" TUI prompt the
first time it runs in a directory it hasn't seen. Without it being dismissed,
the agent stalls there indefinitely (Talos eventually kills it on the
idle-output timeout). Talos creates a fresh worktree per run, so claude has
*never* seen those paths and would prompt every time.

We pre-write the same trust marker the dialog would set ("hasTrustDialogAccepted":
true) for the worktree path before spawning. The host operator's claude shares
the file, so we hold a flock for the read-modify-write to avoid clobbering a
concurrent host write.
"""
from __future__ import annotations

import fcntl
import json
import os

import structlog

log = structlog.get_logger()


def trust_workdir(workdir: str, *, claude_json_path: str | None = None) -> None:
    """Mark `workdir` as trusted in ~/.claude.json. Best-effort — log and move on
    if the file is missing or malformed; claude will fall back to the dialog
    and Talos's idle timer will catch the stall."""
    path = claude_json_path or os.path.join(os.path.expanduser("~"), ".claude.json")
    abs_workdir = os.path.abspath(workdir)

    try:
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read()
                if not raw:
                    log.warning("claude_trust.empty_file", path=path)
                    return
                data = json.loads(raw)
                projects = data.setdefault("projects", {})
                entry = projects.setdefault(abs_workdir, {})
                if entry.get("hasTrustDialogAccepted") is True:
                    return  # already trusted, no write needed
                entry["hasTrustDialogAccepted"] = True
                entry.setdefault("projectOnboardingSeenCount", 1)
                # In-place rewrite — claude.json is a bind-mounted file, so
                # os.replace() fails with EBUSY (can't swap a mount's inode).
                # The flock above serialises us against host-claude writers.
                serialized = json.dumps(data, separators=(",", ":"))
                fh.seek(0)
                fh.write(serialized)
                fh.truncate()
                fh.flush()
                os.fsync(fh.fileno())
                log.info("claude_trust.added", workdir=abs_workdir)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        log.warning("claude_trust.file_missing", path=path)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("claude_trust.failed", path=path, error=str(exc))
