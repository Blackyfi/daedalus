"""Behaviour tests for the PTY input-holder helpers in daedalus.iris.main.

The helpers only call a small subset of the redis client surface, so we use a
hand-rolled in-memory fake instead of pulling in fakeredis as a dependency.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

import pytest

from daedalus.iris import main as iris


@dataclass
class _FakeUser:
    id: uuid.UUID
    email: str
    display_name: str


class _FakeRedis:
    """Bare-minimum async redis stand-in for the holder helpers."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._kv.pop(key, None) is not None else 0

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._kv

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


def _make_user(email: str = "alice@example.com", name: str = "Alice") -> _FakeUser:
    return _FakeUser(id=uuid.uuid4(), email=email, display_name=name)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_first_set_only_if_vacant_wins() -> None:
    async def go() -> None:
        r = _FakeRedis()
        u = _make_user()
        key = iris._holder_key("run-1")

        await iris._set_pty_holder(r, key, "conn-A", u, only_if_vacant=True)
        first = await iris._read_pty_holder(r, key)
        assert first is not None
        assert first["connection_id"] == "conn-A"
        assert first["user_email"] == "alice@example.com"

        # Second vacant-only attempt by a different connection is a no-op.
        u2 = _make_user("bob@example.com", "Bob")
        await iris._set_pty_holder(r, key, "conn-B", u2, only_if_vacant=True)
        still = await iris._read_pty_holder(r, key)
        assert still is not None
        assert still["connection_id"] == "conn-A"

    _run(go())


def test_force_set_replaces_holder() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user("alice@example.com")
        b = _make_user("bob@example.com")
        key = iris._holder_key("run-1")

        await iris._set_pty_holder(r, key, "conn-A", a, only_if_vacant=True)
        await iris._set_pty_holder(r, key, "conn-B", b, only_if_vacant=False)

        cur = await iris._read_pty_holder(r, key)
        assert cur is not None
        assert cur["connection_id"] == "conn-B"
        assert cur["user_email"] == "bob@example.com"

    _run(go())


def test_release_only_if_owned() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user()
        chan = iris._state_chan("run-1")
        key = iris._holder_key("run-1")

        await iris._set_pty_holder(r, key, "conn-A", a, only_if_vacant=True)

        # Wrong connection trying to release — no-op, no event.
        await iris._release_pty_holder(r, key, chan, "conn-OTHER")
        assert await iris._read_pty_holder(r, key) is not None
        assert r.published == []

        # Real owner releases — key clears, "released" event fires.
        await iris._release_pty_holder(r, key, chan, "conn-A")
        assert await iris._read_pty_holder(r, key) is None
        assert any(c == chan and json.loads(m).get("kind") == "released"
                   for c, m in r.published)

    _run(go())


def test_handle_input_only_routed_when_holder() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user()
        rid = "run-x"
        key = iris._holder_key(rid)
        chan = iris._state_chan(rid)
        signal_chan = f"hermes:signal:{rid}"

        await iris._set_pty_holder(r, key, "conn-A", a, only_if_vacant=True)

        # Holder sends input → forwarded to talos.
        await iris._handle_pty_client_msg(
            json.dumps({"t": "input", "d": "ls\n"}),
            r, key, chan, "conn-A", a, rid,
        )
        assert any(c == signal_chan and json.loads(m).get("text") == "ls\n"
                   for c, m in r.published)

        # Non-holder sends input → dropped.
        r.published.clear()
        await iris._handle_pty_client_msg(
            json.dumps({"t": "input", "d": "rm -rf /\n"}),
            r, key, chan, "conn-OTHER", _make_user("evil@x"), rid,
        )
        assert all(c != signal_chan for c, _ in r.published)

    _run(go())


def test_takeover_publishes_state() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user("alice@example.com")
        b = _make_user("bob@example.com")
        rid = "run-y"
        key = iris._holder_key(rid)
        chan = iris._state_chan(rid)

        await iris._set_pty_holder(r, key, "conn-A", a, only_if_vacant=True)
        r.published.clear()

        await iris._handle_pty_client_msg(
            json.dumps({"t": "takeover"}),
            r, key, chan, "conn-B", b, rid,
        )

        cur = await iris._read_pty_holder(r, key)
        assert cur is not None
        assert cur["connection_id"] == "conn-B"
        assert cur["user_email"] == "bob@example.com"
        assert any(c == chan and json.loads(m).get("kind") == "takeover"
                   for c, m in r.published)

    _run(go())


def test_release_message_only_if_owned() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user()
        rid = "run-z"
        key = iris._holder_key(rid)
        chan = iris._state_chan(rid)

        await iris._set_pty_holder(r, key, "conn-A", a, only_if_vacant=True)

        # Non-holder release → no-op.
        await iris._handle_pty_client_msg(
            json.dumps({"t": "release"}),
            r, key, chan, "conn-OTHER", a, rid,
        )
        assert await iris._read_pty_holder(r, key) is not None

        # Holder release → cleared.
        await iris._handle_pty_client_msg(
            json.dumps({"t": "release"}),
            r, key, chan, "conn-A", a, rid,
        )
        assert await iris._read_pty_holder(r, key) is None

    _run(go())


def test_unknown_message_kind_does_nothing() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user()
        rid = "run-q"
        key = iris._holder_key(rid)
        chan = iris._state_chan(rid)
        await iris._handle_pty_client_msg(
            json.dumps({"t": "wat"}),
            r, key, chan, "conn-A", a, rid,
        )
        assert r.published == []
        assert await iris._read_pty_holder(r, key) is None

    _run(go())


def test_malformed_message_swallowed() -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user()
        rid = "run-q"
        await iris._handle_pty_client_msg(
            "not-json-at-all", r, iris._holder_key(rid),
            iris._state_chan(rid), "conn-A", a, rid,
        )
        await iris._handle_pty_client_msg(
            json.dumps([1, 2, 3]),  # not a dict
            r, iris._holder_key(rid),
            iris._state_chan(rid), "conn-A", a, rid,
        )
        assert r.published == []

    _run(go())


@pytest.mark.parametrize("kind", ["takeover", "release", "input", "ping"])
def test_dispatch_smoke(kind: str) -> None:
    async def go() -> None:
        r = _FakeRedis()
        a = _make_user()
        rid = "run-s"
        await iris._set_pty_holder(r, iris._holder_key(rid), "conn-A", a, only_if_vacant=True)
        msg: dict = {"t": kind}
        if kind == "input":
            msg["d"] = "x"
        await iris._handle_pty_client_msg(
            json.dumps(msg),
            r, iris._holder_key(rid), iris._state_chan(rid),
            "conn-A", a, rid,
        )
        # No exception ⇒ pass; also check that we didn't blow away the holder
        # for non-release ops.
        if kind != "release":
            assert await iris._read_pty_holder(r, iris._holder_key(rid)) is not None

    _run(go())
