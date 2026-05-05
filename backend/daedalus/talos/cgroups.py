"""cgroups v2 enforcement for Talos-spawned agent processes.

We assume the unified hierarchy is mounted at /sys/fs/cgroup (the modern
default on systemd-init Linux). The Talos container needs CAP_SYS_ADMIN and
either to run in the host cgroup namespace, or have its own cgroup made
writable.

Per spec §6.2 we honour:
  - cpu_shares        → cpu.weight (1..10000; cgroup-v2 default 100)
  - memory_mb         → memory.max (bytes)
  - pids_max          → pids.max
  - wall_clock / idle_output are still policed by the runner's poll loop.

If anything fails (no v2 hierarchy, no permission, host-level overlay), we
log and continue without enforcement so the platform stays usable on dev
laptops and macOS.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()


CGROUP_ROOT = Path("/sys/fs/cgroup")
DAEDALUS_PARENT = CGROUP_ROOT / "daedalus.slice"


def is_cgroup_v2_available() -> bool:
    """Unified hierarchy = a cgroup.controllers file at the root."""
    return (CGROUP_ROOT / "cgroup.controllers").exists()


def _enable_controllers_in_parent(parent: Path, controllers: list[str]) -> None:
    """`echo +cpu +memory +pids > parent/cgroup.subtree_control`. Idempotent."""
    subtree = parent / "cgroup.subtree_control"
    if not subtree.exists():
        return
    payload = " ".join(f"+{c}" for c in controllers)
    try:
        subtree.write_text(payload)
    except OSError as exc:
        log.debug("cgroup.subtree_control_failed", parent=str(parent), error=str(exc))


@dataclass
class RunCgroup:
    path: Path

    def add_pid(self, pid: int) -> bool:
        """Move *pid* (and its descendants by default in v2) into this cgroup."""
        try:
            (self.path / "cgroup.procs").write_text(str(pid))
            return True
        except OSError as exc:
            log.warning("cgroup.add_pid_failed", pid=pid, cgroup=str(self.path), error=str(exc))
            return False

    def remove(self) -> None:
        """Best-effort `rmdir` once all processes have exited."""
        try:
            os.rmdir(self.path)
        except OSError:
            pass


def create_run_cgroup(
    run_id: str,
    *,
    cpu_shares: int | None = None,
    memory_mb: int | None = None,
    pids_max: int | None = None,
) -> RunCgroup | None:
    """Create /sys/fs/cgroup/daedalus.slice/run-<id>/ with the given limits.
    Returns None if v2 isn't available or if mkdir fails."""
    if not is_cgroup_v2_available():
        log.info("cgroup.v2_unavailable", run_id=run_id)
        return None

    parent = _ensure_parent()
    if parent is None:
        return None

    cg_path = parent / f"run-{run_id}"
    try:
        cg_path.mkdir(exist_ok=True)
    except OSError as exc:
        log.warning("cgroup.mkdir_failed", path=str(cg_path), error=str(exc))
        return None

    if cpu_shares is not None and cpu_shares > 0:
        _write_limit(cg_path, "cpu.weight", _cpu_shares_to_v2_weight(cpu_shares))
    if memory_mb is not None and memory_mb > 0:
        _write_limit(cg_path, "memory.max", memory_mb * 1024 * 1024)
    if pids_max is not None and pids_max > 0:
        _write_limit(cg_path, "pids.max", pids_max)

    log.info(
        "cgroup.created",
        run_id=run_id,
        path=str(cg_path),
        cpu_shares=cpu_shares,
        memory_mb=memory_mb,
        pids_max=pids_max,
    )
    return RunCgroup(path=cg_path)


def _ensure_parent() -> Path | None:
    """Make sure /sys/fs/cgroup/daedalus.slice exists with the controllers we need."""
    try:
        DAEDALUS_PARENT.mkdir(exist_ok=True)
    except OSError as exc:
        log.warning("cgroup.parent_mkdir_failed", error=str(exc))
        return None
    _enable_controllers_in_parent(CGROUP_ROOT, ["cpu", "memory", "pids"])
    _enable_controllers_in_parent(DAEDALUS_PARENT, ["cpu", "memory", "pids"])
    return DAEDALUS_PARENT


def _write_limit(cg_path: Path, key: str, value: int) -> None:
    target = cg_path / key
    try:
        target.write_text(str(value))
    except OSError as exc:
        log.warning("cgroup.write_failed", file=str(target), error=str(exc))


def _cpu_shares_to_v2_weight(cpu_shares: int) -> int:
    """v1 cpu.shares (default 1024) → v2 cpu.weight (default 100, range 1..10000).

    Linear mapping clamped to v2's range. cpu_shares 1024 → cpu.weight 100.
    """
    weight = max(1, min(10000, round(cpu_shares * 100 / 1024)))
    return weight
