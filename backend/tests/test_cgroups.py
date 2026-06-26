"""Unit tests for the cgroups v2 helper. We point the helper at a tmp_path
so the tests work everywhere — even if the host has cgroup v1 / no privs."""
from __future__ import annotations

import pytest

from daedalus.talos import cgroups


@pytest.fixture
def fake_cgroup_root(tmp_path, monkeypatch):
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu memory pids")
    (root / "cgroup.subtree_control").write_text("")
    monkeypatch.setattr(cgroups, "CGROUP_ROOT", root)
    monkeypatch.setattr(cgroups, "DAEDALUS_PARENT", root / "daedalus.slice")
    return root


def test_is_cgroup_v2_available(fake_cgroup_root):
    assert cgroups.is_cgroup_v2_available()


def test_create_run_cgroup_writes_limits(fake_cgroup_root):
    cg = cgroups.create_run_cgroup(
        "abc-123", cpu_shares=2048, memory_mb=512, pids_max=64
    )
    assert cg is not None
    assert cg.path == fake_cgroup_root / "daedalus.slice" / "run-abc-123"

    # cpu_shares 2048 → cpu.weight 200
    assert (cg.path / "cpu.weight").read_text() == "200"
    assert (cg.path / "memory.max").read_text() == str(512 * 1024 * 1024)
    assert (cg.path / "pids.max").read_text() == "64"


def test_create_run_cgroup_skips_when_v2_missing(tmp_path, monkeypatch):
    root = tmp_path / "no-v2"
    root.mkdir()
    monkeypatch.setattr(cgroups, "CGROUP_ROOT", root)
    monkeypatch.setattr(cgroups, "DAEDALUS_PARENT", root / "daedalus.slice")
    assert cgroups.create_run_cgroup("x", cpu_shares=1024) is None


def test_cpu_shares_to_v2_weight_clamps():
    assert cgroups._cpu_shares_to_v2_weight(1024) == 100
    assert cgroups._cpu_shares_to_v2_weight(0) == 1
    assert cgroups._cpu_shares_to_v2_weight(10**9) == 10000
