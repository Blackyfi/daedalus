"""Connector pack importer used by `import-connectors` CLI and the
`POST /connectors/reload` endpoint."""
from __future__ import annotations

import json

import pytest

from daedalus.connectors.loader import ConnectorImportError, import_connectors_from_dir

_VALID_SPEC = {
    "id": "test-conn",
    "display_name": "Test Connector",
    "command": "echo",
    "workdir": "/tmp",
    "permission_profile": "confirm",
    "input_format": {"kind": "stdin_prompt"},
    "done_signal": {"kind": "exit_code", "exit_code": 0},
}


class _FakeResult:
    def scalar_one_or_none(self):
        return None  # always "not present" → every spec is an insert


class _FakeDB:
    def __init__(self) -> None:
        self.added: list = []

    async def execute(self, *a, **k):
        return _FakeResult()

    def add(self, obj) -> None:
        self.added.append(obj)


def _write(dir_, name, payload) -> None:
    (dir_ / name).write_text(payload if isinstance(payload, str) else json.dumps(payload))


@pytest.mark.asyncio
async def test_imports_valid_pack(tmp_path) -> None:
    _write(tmp_path, "a.json", _VALID_SPEC)
    _write(tmp_path, "b.json", {**_VALID_SPEC, "id": "test-conn-2"})
    db = _FakeDB()
    summary = await import_connectors_from_dir(db, tmp_path)
    assert summary["imported"] == 2
    assert summary["added"] == 2
    assert summary["updated"] == 0
    assert summary["connector_ids"] == ["test-conn", "test-conn-2"]
    assert len(db.added) == 2


@pytest.mark.asyncio
async def test_empty_dir_raises(tmp_path) -> None:
    with pytest.raises(ConnectorImportError):
        await import_connectors_from_dir(_FakeDB(), tmp_path)


@pytest.mark.asyncio
async def test_malformed_json_raises(tmp_path) -> None:
    _write(tmp_path, "bad.json", "{ not json")
    with pytest.raises(ConnectorImportError):
        await import_connectors_from_dir(_FakeDB(), tmp_path)


@pytest.mark.asyncio
async def test_schema_invalid_spec_raises(tmp_path) -> None:
    _write(tmp_path, "missing.json", {"id": "x", "display_name": "x"})  # missing required fields
    with pytest.raises(ConnectorImportError):
        await import_connectors_from_dir(_FakeDB(), tmp_path)
