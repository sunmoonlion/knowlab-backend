"""本应用自己的对象存储桶（common/backend/storage）。

用户上传的数据只进这里（SDD 0027 决定 4）。

桶开了版本控制：每次写入回版本号，元数据带 sha256，读回按版本取并核对。部署的
application-check 用这个类在真实 API 角色里做读写核对（storage/verify-runtime.py 要
`s3_client()`、`bucket`、`put_bytes`），所以这几个名字与返回形状不要改。
只开 S3：没接时抛 StorageUnavailable，不回退到容器本地目录。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.config import Settings, get_settings


class StorageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    object_key: str
    version_id: str | None
    sha256: str
    size_bytes: int
    content_type: str


class ObjectStorage:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return self._settings.storage_backend.lower() == "s3"

    @property
    def bucket(self) -> str:
        return self._settings.s3_bucket

    def put_bytes(
        self,
        *,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        sha256 = hashlib.sha256(data).hexdigest()
        client = self.s3_client()
        try:
            response = client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=data,
                ContentType=content_type,
                Metadata=dict(metadata or {}) | {"sha256": sha256},
            )
            version_id = response.get("VersionId")
            if not version_id or version_id.lower() == "null":
                raise RuntimeError("s3_version_required")
            # 只认 PUT 回来的那个版本：别的写入可能在 PUT 与 HEAD 之间又写了一版
            head = client.head_object(
                Bucket=self.bucket, Key=object_key, VersionId=version_id
            )
            if (
                head.get("VersionId") != version_id
                or int(head["ContentLength"]) != len(data)
                or head.get("Metadata", {}).get("sha256") != sha256
            ):
                raise RuntimeError("s3_version_metadata_mismatch")
            return StoredObject(
                bucket=self.bucket,
                object_key=object_key,
                version_id=version_id,
                sha256=sha256,
                size_bytes=len(data),
                content_type=content_type,
            )
        finally:
            client.close()

    def get_bytes(
        self,
        *,
        object_key: str,
        version_id: str,
        expected_sha256: str,
        max_bytes: int,
    ) -> bytes:
        """按版本读回并核对 sha256；对不上不返回内容。"""
        client = self.s3_client()
        try:
            response = client.get_object(
                Bucket=self.bucket, Key=object_key, VersionId=version_id
            )
            try:
                if int(response["ContentLength"]) > max_bytes:
                    raise RuntimeError("stored_object_too_large")
                data = response["Body"].read(max_bytes + 1)
            finally:
                response["Body"].close()
        finally:
            client.close()
        if len(data) > max_bytes:
            raise RuntimeError("stored_object_too_large")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise RuntimeError("stored_object_digest_mismatch")
        return data

    def delete(self, *, object_key: str) -> None:
        """删掉当前版本（打删除标记）。

        历史版本按桶策略保留，应用删不了（见部署的 common/backend/storage/README.md）。
        """
        client = self.s3_client()
        try:
            client.delete_object(Bucket=self.bucket, Key=object_key)
        finally:
            client.close()

    def s3_client(self) -> Any:
        """本应用自己的 S3 客户端；调用方负责 close。"""
        if not self.enabled:
            raise StorageUnavailable("object storage is not configured")
        import boto3
        from botocore.config import Config

        addressing_style = "path" if self._settings.s3_force_path_style else "virtual"
        return boto3.client(
            "s3",
            endpoint_url=self._settings.s3_endpoint,
            region_name=self._settings.s3_region,
            aws_access_key_id=self._settings.s3_access_key_id,
            aws_secret_access_key=self._settings.s3_secret_access_key,
            use_ssl=self._settings.s3_use_tls,
            config=Config(
                s3={"addressing_style": addressing_style},
                connect_timeout=3,
                read_timeout=10,
                retries={"mode": "standard", "total_max_attempts": 2},
            ),
        )
