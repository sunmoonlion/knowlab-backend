"""用户的知识库（SDD 0027 决定 4、SDD 0042 第七节）。

上传、列出、删除、测试同意、搬旧数据。10-23 起知识库的资料放在 knowbase 的
knowlab 分区（配了 knowbase 时）：网页 → 这里凭本人令牌传、删，这里只记业务记录
（数据编号、名字、类型、大小、sha256、knowbase 的资料编号）。清单的绑定、格子地址、
引文都写 knowlab 的数据编号，所以搬家前后编号对得上、摘要不变。没配 knowbase 时
照旧放本应用的对象存储。

测试同意：本人传、删、打开知识库页时（本人在场），凭本人令牌在 knowbase 更新一条
「测试」同意依据，范围是他知识库里的全部资料；发起测试时凭它签测试运行授权
（knowbot_service）。

先存资料再记元数据：资料存好了、元数据没记上，只多一份没人引用的资料，不会有指向
空处的记录。删除先核引用（在做的、已交付的草稿绑着的不能删），再删资料、再记删除。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import (
    DatasetRecord,
    DataStore,
    KnowlabRepository,
    KnowledgeBase,
)
from app.domain.knowbots import datasets
from app.domain.knowbots.datasets import Rejected

log = logging.getLogger(__name__)

UserToken = Callable[[], Awaitable[str]]
# 测试同意在 knowbase 里的编号写法：一个用户一条，范围是他 knowlab 知识库的全部资料
CONSENT_REF = "knowlab-kb"
MAX_CONSENT_ITEMS = 1000  # knowbase 一条同意依据最多这么多份


def _parse(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise NotFoundError("No such data", code="dataset_not_found") from exc


def consent_items(records: Sequence[DatasetRecord]) -> list[dict[str, str]]:
    """测试同意的范围：在 knowbase 里的、没删的，按资料编号排。"""
    found = sorted(
        {(d.item_id, d.sha256) for d in records if d.item_id and d.deleted_at is None}
    )
    return [{"item_id": i, "sha256": h} for i, h in found[:MAX_CONSENT_ITEMS]]


def items_digest(items: list[dict[str, str]]) -> str:
    body = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class KnowbotDataService:
    def __init__(
        self,
        repo: KnowlabRepository,
        store: DataStore,
        *,
        knowledge: KnowledgeBase | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.store = store
        self.knowledge = knowledge
        self.now = now

    async def mine(self, owner_subject: str) -> tuple[Sequence[DatasetRecord], int]:
        found = await self.repo.list_datasets(owner_subject)
        return found, sum(d.size_bytes for d in found)

    async def upload(
        self,
        owner_subject: str,
        filename: str | None,
        data: bytes,
        user_token: UserToken | None = None,
    ) -> DatasetRecord:
        if self.knowledge is None and not self.store.enabled:
            raise ServiceUnavailableError(
                "Uploading data is not available yet", code="storage_unavailable"
            )
        try:
            name = datasets.clean_name(filename)
            content_type = datasets.content_type_of(name, data)
            datasets.check_size(len(data), 0)
        except Rejected as exc:
            raise BadRequestError(exc.message, code=exc.code) from None
        if self.knowledge is not None:
            return await self._upload_to_knowbase(
                owner_subject, name, content_type, data, user_token
            )
        dataset_id = uuid.uuid4()
        key = datasets.object_key(owner_subject, str(dataset_id))
        async with self.repo.transaction():
            await self.repo.lock_owner_data(owner_subject)
            await self._check_room(owner_subject, len(data))
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

    async def delete(
        self, owner_subject: str, dataset_id: str, user_token: UserToken | None = None
    ) -> None:
        did = _parse(dataset_id)
        token = None
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
            if found.item_id and self.knowledge is not None:
                token = await self._token(user_token)
                await self.knowledge.delete(token, found.item_id)
            await self.repo.mark_dataset_deleted(did, self.now())
        if found.object_key and self.store.enabled:
            await self.store.delete(object_key=found.object_key)
        if token is not None:
            await self._refresh_consent(owner_subject, token)

    async def ensure_consent(self, owner_subject: str, user_token: UserToken) -> None:
        """本人打开知识库页时：测试同意的范围对不上就更新。

        搬家后的旧数据靠这一步补上同意。

        更新不成不挡页面：测试照旧经 knowlab 读，下次再补。
        """
        if self.knowledge is None:
            return
        items = consent_items(await self.repo.list_datasets(owner_subject))
        current = await self.repo.get_test_consent(owner_subject)
        if (current is None and not items) or (
            current is not None and current["items_digest"] == items_digest(items)
        ):
            return
        try:
            await self._refresh_consent(owner_subject, await user_token())
        except AppException as exc:
            log.warning("test consent not refreshed code=%s", exc.code)

    async def import_old(self, limit: int = 50) -> int:
        """把还在本应用对象存储里的旧数据搬进 knowbase。

        一次性；按数据编号幂等，可以重跑。

        旧对象不删：留到「财务分析与预测」第 1 版停用（第 1 版运行时照旧经
        knowlab 读）。
        """
        if self.knowledge is None:
            raise ServiceUnavailableError(
                "knowbase is not configured", code="knowbase_unconfigured"
            )
        moved = 0
        async with self.repo.transaction():
            pending = list(await self.repo.datasets_to_import(limit))
        for record in pending:
            assert record.object_key and record.version_id
            data = await self.store.get(
                object_key=record.object_key,
                version_id=record.version_id,
                sha256=record.sha256,
                max_bytes=datasets.MAX_FILE_BYTES,
            )
            item = await self.knowledge.import_item(
                owner=record.owner_subject,
                name=record.name,
                data=data,
                ref=f"knowlab-dataset:{record.id}",
            )
            if item.get("sha256") != record.sha256:
                raise ServiceUnavailableError(
                    "knowbase answered another digest", code="import_mismatch"
                )
            async with self.repo.transaction():
                await self.repo.set_dataset_item(record.id, str(item["item_id"]))
            moved += 1
        return moved

    # ---------- 内部 ----------

    async def _upload_to_knowbase(
        self,
        owner_subject: str,
        name: str,
        content_type: str,
        data: bytes,
        user_token: UserToken | None,
    ) -> DatasetRecord:
        assert self.knowledge is not None
        async with self.repo.transaction():
            await self.repo.lock_owner_data(owner_subject)
            await self._check_room(owner_subject, len(data))
        token = await self._token(user_token)
        item = await self.knowledge.upload(token, name, data)
        async with self.repo.transaction():
            await self.repo.lock_owner_data(owner_subject)
            created = await self.repo.create_dataset(
                DatasetRecord(
                    id=uuid.uuid4(),
                    owner_subject=owner_subject,
                    name=name,
                    version=datasets.DATASET_VERSION,
                    content_type=content_type,
                    size_bytes=len(data),
                    sha256=str(item["sha256"]),
                    object_key=None,
                    version_id=None,
                    item_id=str(item["item_id"]),
                )
            )
        await self._refresh_consent(owner_subject, token)
        return created

    async def _check_room(self, owner_subject: str, size: int) -> None:
        found, used = await self.mine(owner_subject)
        try:
            datasets.check_size(size, used)
            datasets.check_count(len(found))
        except Rejected as exc:
            raise BadRequestError(exc.message, code=exc.code) from None

    async def _token(self, user_token: UserToken | None) -> str:
        if user_token is None:
            raise ServiceUnavailableError(
                "knowbase is not configured", code="knowbase_unconfigured"
            )
        return await user_token()

    async def _refresh_consent(self, owner_subject: str, token: str) -> None:
        """测试同意跟上知识库：立一条新的、记下，再撤旧的（撤不成只记一行）。"""
        assert self.knowledge is not None
        items = consent_items(await self.repo.list_datasets(owner_subject))
        current = await self.repo.get_test_consent(owner_subject)
        digest = items_digest(items)
        if current is not None and current["items_digest"] == digest:
            return
        consent_id = (
            await self.knowledge.consent(token, CONSENT_REF, items) if items else None
        )
        async with self.repo.transaction():
            if consent_id is None:
                await self.repo.delete_test_consent(owner_subject)
            else:
                await self.repo.put_test_consent(owner_subject, consent_id, digest)
        if current is not None:
            try:
                await self.knowledge.withdraw(token, current["consent_id"])
            except AppException as exc:
                log.warning("old test consent not withdrawn code=%s", exc.code)
