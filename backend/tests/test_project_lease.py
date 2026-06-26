"""Project-lease primitives — exercised against a fake redis stub.

Covers the atomic claim path's edge cases:
- happy path
- cap reached
- project already busy
- payload no longer in queue (lost LREM race)
- release is idempotent
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from daedalus.hermes import leases


class FakeRedis:
    """Tiny in-memory redis stub. Implements only what the lease module uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[bytes]] = {}
        self.sets: dict[str, set[str]] = {}

    # eval mimics the Lua claim script.
    async def eval(
        self,
        script: str,
        numkeys: int,
        *args,
    ) -> bytes | None:
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        queue_key, lease_key, active_set_key = keys
        run_id, _ttl, max_concurrent, payload, project_id = argv

        max_concurrent = int(max_concurrent)
        if len(self.sets.get(active_set_key, set())) >= max_concurrent:
            return None
        if lease_key in self.kv:
            return None
        bucket = self.lists.get(queue_key, [])
        try:
            bucket.remove(payload if isinstance(payload, bytes) else payload.encode())
        except ValueError:
            return None
        self.kv[lease_key] = run_id
        self.sets.setdefault(active_set_key, set()).add(project_id)
        return payload if isinstance(payload, bytes) else payload.encode()

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.kv.pop(key, None) is not None else 0

    async def srem(self, key: str, *members: str) -> int:
        s = self.sets.get(key, set())
        n = 0
        for m in members:
            if m in s:
                s.discard(m)
                n += 1
        return n

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    async def expire(self, key: str, ttl: int) -> int:
        return 1 if key in self.kv else 0

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self) -> None:
                self.ops: list[Any] = []

            def delete(self, key: str) -> _Pipe:
                self.ops.append(("delete", key))
                return self

            def srem(self, key: str, *members: str) -> _Pipe:
                self.ops.append(("srem", key, members))
                return self

            async def execute(self) -> None:
                for op in self.ops:
                    if op[0] == "delete":
                        await outer.delete(op[1])
                    elif op[0] == "srem":
                        await outer.srem(op[1], *op[2])

        return _Pipe()


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.mark.asyncio
async def test_claim_happy_path(fake_redis: FakeRedis):
    queue_key = "hermes:queue:default"
    payload = json.dumps(
        {"run_id": "r1", "kind": "task", "project_id": "p1", "lane": "default"}
    ).encode()
    fake_redis.lists[queue_key] = [payload]

    with patch("daedalus.hermes.leases.get_redis", return_value=fake_redis):
        result = await leases.try_claim(
            queue_key=queue_key,
            project_id="p1",
            run_id="r1",
            payload=payload,
            max_concurrent_projects=4,
            lease_ttl_seconds=600,
        )
        assert result.succeeded
        assert result.payload == payload
        assert "p1" in await leases.active_projects()
        assert await leases.is_project_busy("p1")


@pytest.mark.asyncio
async def test_claim_blocked_when_project_busy(fake_redis: FakeRedis):
    queue_key = "hermes:queue:default"
    payload = b"{}"
    fake_redis.lists[queue_key] = [payload]
    fake_redis.kv[leases.project_lease_key("p1")] = "other-run"

    with patch("daedalus.hermes.leases.get_redis", return_value=fake_redis):
        result = await leases.try_claim(
            queue_key=queue_key,
            project_id="p1",
            run_id="r1",
            payload=payload,
            max_concurrent_projects=4,
            lease_ttl_seconds=600,
        )
    assert not result.succeeded
    # And the queue entry must be untouched.
    assert fake_redis.lists[queue_key] == [payload]


@pytest.mark.asyncio
async def test_claim_blocked_when_cap_reached(fake_redis: FakeRedis):
    queue_key = "hermes:queue:default"
    payload = b"{}"
    fake_redis.lists[queue_key] = [payload]
    # Two projects already busy with cap=2.
    fake_redis.sets[leases.ACTIVE_PROJECTS_KEY] = {"px", "py"}

    with patch("daedalus.hermes.leases.get_redis", return_value=fake_redis):
        result = await leases.try_claim(
            queue_key=queue_key,
            project_id="p1",
            run_id="r1",
            payload=payload,
            max_concurrent_projects=2,
            lease_ttl_seconds=600,
        )
    assert not result.succeeded
    assert fake_redis.lists[queue_key] == [payload]
    assert leases.project_lease_key("p1") not in fake_redis.kv


@pytest.mark.asyncio
async def test_claim_lost_race_when_payload_already_removed(fake_redis: FakeRedis):
    """If another worker LREM'd the entry first, our claim must fail without
    leaking a partial lease."""
    queue_key = "hermes:queue:default"
    payload = b"{}"
    # Empty queue — simulating a competing worker that already popped.
    fake_redis.lists[queue_key] = []

    with patch("daedalus.hermes.leases.get_redis", return_value=fake_redis):
        result = await leases.try_claim(
            queue_key=queue_key,
            project_id="p1",
            run_id="r1",
            payload=payload,
            max_concurrent_projects=4,
            lease_ttl_seconds=600,
        )
    assert not result.succeeded
    assert leases.project_lease_key("p1") not in fake_redis.kv
    assert "p1" not in fake_redis.sets.get(leases.ACTIVE_PROJECTS_KEY, set())


@pytest.mark.asyncio
async def test_release_is_idempotent(fake_redis: FakeRedis):
    fake_redis.kv[leases.project_lease_key("p1")] = "r1"
    fake_redis.sets[leases.ACTIVE_PROJECTS_KEY] = {"p1"}

    with patch("daedalus.hermes.leases.get_redis", return_value=fake_redis):
        await leases.release_lease("p1")
        # Second call — must not raise, must keep state empty.
        await leases.release_lease("p1")

    assert leases.project_lease_key("p1") not in fake_redis.kv
    assert "p1" not in fake_redis.sets.get(leases.ACTIVE_PROJECTS_KEY, set())


@pytest.mark.asyncio
async def test_clear_stale_leases_drops_orphans(fake_redis: FakeRedis):
    """When a Hermes restart finds the active set referencing dead lease keys,
    those entries must be cleared so cap accounting reflects reality."""
    fake_redis.kv[leases.project_lease_key("alive")] = "alive-run"
    fake_redis.sets[leases.ACTIVE_PROJECTS_KEY] = {"alive", "ghost"}

    with patch("daedalus.hermes.leases.get_redis", return_value=fake_redis):
        removed = await leases.clear_stale_leases({"alive-run"})

    assert removed == 1
    assert "ghost" not in fake_redis.sets[leases.ACTIVE_PROJECTS_KEY]
    assert "alive" in fake_redis.sets[leases.ACTIVE_PROJECTS_KEY]


def test_parse_payload_handles_garbage():
    assert leases.parse_payload(b"not-json") is None
    assert leases.parse_payload(b"\xff\xfe") is None
    assert leases.parse_payload(b'{"run_id":"x"}') == {"run_id": "x"}
