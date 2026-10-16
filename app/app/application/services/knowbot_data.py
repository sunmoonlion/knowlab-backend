"""用户上传的数据（SDD 0027 决定 4）：上传、列出、删除。

先存对象再记元数据：对象存好了、元数据没记上，只多一个没人引用的对象，不会有指向空处的记录。
删除先核引用（在做的、已交付的草稿绑着的不能删），再记删除、再删对象。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import DatasetRecord, DataStore, KnowlabRepository
from app.domain.knowbots import datasets
from app.domain.knowbots.datasets import Rejected


def _parse(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise NotFoundError("No such data", code="dataset_not_found") from exc


class KnowbotDataService:
    def __init__(
        self,
        repo: KnowlabRepository,
        store: DataStore,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.store = store
        self.now = now

    async def mine(self, owner_subject: str) -> tuple[Sequence[DatasetRecord], int]:
        found = await self.repo.list_datasets(owner_subject)
        return found, sum(d.size_bytes for d in found)

    async def upload(
        self, owner_subject: str, filename: str | None, data: bytes
    ) -> DatasetRecord:
        if not self.store.enabled:
            raise ServiceUnavailableError(
                "Uploading data is not available yet", code="storage_unavailable"
            )
        try:
            name = datasets.clean_name(filename)
            content_type = datasets.content_type_of(name, data)
            datasets.check_size(len(data), 0)
        except Rejected as exc:
            raise BadRequestError(exc.message, code=exc.code) from None
        dataset_id = uuid.uuid4()
        key = datasets.object_key(owner_subject, str(dataset_id))
        async with self.repo.transaction():
            await self.repo.lock_owner_data(owner_subject)
            _, used = await self.mine(owner_subject)
            try:
                datasets.check_size(len(data), used)
            except Rejected as exc:
                raise BadRequestError(exc.message, code=exc.code) from None
            version_id, sha256 = await self.store.put(
                object_key=key, data=data, content_type=content_type
            )
            return await self.repo.create_dataset(
                DatasetRecord(
                    id=dataset_id,
                    owner_subject=owner_subject,
                    name=name,
                    version=datasets.DATASET_VERSION,
                    content_type=content_type,
                    size_bytes=len(data),
                    sha256=sha256,
                    object_key=key,
                    version_id=version_id,
                )
            )

    async def delete(self, owner_subject: str, dataset_id: str) -> None:
        did = _parse(dataset_id)
        async with self.repo.transaction():
            await self.repo.lock_owner_data(owner_subject)
            found = await self.repo.get_dataset(did)
            if (
                found is None
                or found.owner_subject != owner_subject
                or found.deleted_at is not None
            ):
                raise NotFoundError("No such data", code="dataset_not_found")
            if await self.repo.dataset_in_use(owner_subject, did):
                raise ConcurrencyConflictError(
                    "This data is used by a knowbot in progress or delivered",
                    code="dataset_in_use",
                )
            await self.repo.mark_dataset_deleted(did, self.now())
        if self.store.enabled:
            await self.store.delete(object_key=found.object_key)
