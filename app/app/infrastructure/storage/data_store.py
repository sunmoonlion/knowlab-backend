"""用户数据存储端口（application/ports/knowbots.DataStore）的实现：本应用的 S3 桶。

boto3 是同步的，放到线程里跑，不堵事件循环。
"""

from __future__ import annotations

import asyncio

from app.infrastructure.storage.object_storage import ObjectStorage


class S3DataStore:
    def __init__(self, storage: ObjectStorage | None = None) -> None:
        self._storage = storage or ObjectStorage()

    @property
    def enabled(self) -> bool:
        return self._storage.enabled

    async def put(
        self, *, object_key: str, data: bytes, content_type: str
    ) -> tuple[str, str]:
        stored = await asyncio.to_thread(
            self._storage.put_bytes,
            object_key=object_key,
            data=data,
            content_type=content_type,
        )
        assert stored.version_id is not None
        return stored.version_id, stored.sha256

    async def delete(self, *, object_key: str) -> None:
        await asyncio.to_thread(self._storage.delete, object_key=object_key)

    async def get(
        self, *, object_key: str, version_id: str, sha256: str, max_bytes: int
    ) -> bytes:
        return await asyncio.to_thread(
            self._storage.get_bytes,
            object_key=object_key,
            version_id=version_id,
            expected_sha256=sha256,
            max_bytes=max_bytes,
        )
