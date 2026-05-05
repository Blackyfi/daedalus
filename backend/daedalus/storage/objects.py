"""S3-compatible object storage with a local filesystem fallback."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config

from daedalus.core.logging import log
from daedalus.core.settings import Settings, get_settings


class ObjectStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = Path(self.settings.objects_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._bucket_checked = False
        self._client = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            config=Config(signature_version="s3v4", connect_timeout=1, read_timeout=2, retries={"max_attempts": 1}),
        )

    def ensure_bucket(self) -> None:
        if self._bucket_checked:
            return
        try:
            self._client.head_bucket(Bucket=self.settings.minio_bucket)
        except Exception:
            try:
                self._client.create_bucket(Bucket=self.settings.minio_bucket)
            except Exception as exc:
                log.warning("object_store.bucket_unavailable", error=str(exc), bucket=self.settings.minio_bucket)
        self._bucket_checked = True

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self.ensure_bucket()
        try:
            self._client.put_object(
                Bucket=self.settings.minio_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            return key
        except Exception as exc:
            log.warning("object_store.put_fallback", key=key, error=str(exc))
            path = self._local_path_for_key(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return key

    def get_bytes(self, key: str) -> bytes:
        self.ensure_bucket()
        try:
            response = self._client.get_object(Bucket=self.settings.minio_bucket, Key=key)
            return response["Body"].read()
        except Exception:
            return self._local_path_for_key(key).read_bytes()

    def put_text(self, key: str, text: str, *, content_type: str = "text/plain; charset=utf-8") -> str:
        return self.put_bytes(key, text.encode("utf-8"), content_type=content_type)

    def get_text(self, key: str) -> str:
        return self.get_bytes(key).decode("utf-8", errors="replace")

    def _local_path_for_key(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError("object key escapes local storage root")
        return candidate


@lru_cache
def get_object_store() -> ObjectStore:
    return ObjectStore()
