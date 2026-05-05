from pathlib import Path

import pytest

from daedalus.core.settings import Settings


def test_object_store_falls_back_to_local_filesystem(tmp_path: Path) -> None:
    pytest.importorskip("structlog")

    from daedalus.storage.objects import ObjectStore

    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost/db",
        redis_url="redis://localhost:6379/0",
        session_secret="secret",
        password_pepper="pepper",
        s3_endpoint="http://127.0.0.1:9",
        s3_access_key="key",
        s3_secret_key="secret",
        minio_bucket="daedalus",
        objects_dir=str(tmp_path),
    )
    store = ObjectStore(settings)

    key = store.put_text("runs/example/transcript.log", "hello transcript")

    assert key == "runs/example/transcript.log"
    assert (tmp_path / "runs" / "example" / "transcript.log").read_text() == "hello transcript"
    assert store.get_text(key) == "hello transcript"
