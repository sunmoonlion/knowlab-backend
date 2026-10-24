"""knowlab 知识库搬进 knowbase（SDD 0042 第七节）。

新传、删走 knowbase；测试同意跟着知识库；旧数据一次性搬；
发起测试签测试运行授权、收尾撤。
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from app.application.errors.exceptions import (
    BadGatewayError,
    BadRequestError,
    ConcurrencyConflictError,
    ServiceUnavailableError,
)
from app.application.services.knowbot_data import (
    CONSENT_REF,
    KnowbotDataService,
    consent_items,
    items_digest,
)
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots import datasets
from app.infrastructure.external.knowbase_user_data import (
    KnowbaseKnowledgeBase,
    KnowbaseRefused,
)
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryDataStore,
    MemoryKnowlab,
    accepted_request,
    manifest,
    result,
)

PDF = b"%PDF-1.7\n..."


class FakeKnowledge:
    """knowbase 的替身：记下传了什么、同意与授权。

    同 knowbase：有没撤的同意含着、或有活动授权读着一份，就不让删它（in_use）。
    """

    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.consents: dict[str, list[dict[str, str]]] = {}
        self.withdrawn: list[str] = []
        self.grants: list[dict[str, Any]] = []
        self.revoked: list[str] = []
        self.imported: list[str] = []
        self.refuse_grant = False
        self.refuse_withdraw = False
        self.reading: set[str] = set()  # 活动授权读着的
        self.tokens: list[str] = []

    async def upload(self, user_token: str, name: str, data: bytes) -> dict[str, Any]:
        self.tokens.append(user_token)
        item_id = f"item-{len(self.items) + 1}"
        self.items[item_id] = data
        return {"item_id": item_id, "sha256": hashlib.sha256(data).hexdigest()}

    async def delete(self, user_token: str, item_id: str) -> None:
        held = any(
            item_id in {i["item_id"] for i in items}
            for c, items in self.consents.items()
            if c not in self.withdrawn
        )
        if held or item_id in self.reading:
            raise ConcurrencyConflictError("in use", code="dataset_in_use")
        self.deleted.append(item_id)

    async def consent(
        self, user_token: str, ref: str, items: list[dict[str, str]]
    ) -> str:
        assert ref == CONSENT_REF
        consent_id = f"consent-{len(self.consents) + 1}"
        self.consents[consent_id] = items
        return consent_id

    async def withdraw(self, user_token: str, consent_id: str) -> None:
        if self.refuse_withdraw:
            raise BadGatewayError("knowbase refused", code="knowbase_refused")
        if consent_id not in self.withdrawn:
            self.withdrawn.append(consent_id)

    async def test_grant(self, *, owner, consent_id, ref, items, ttl_seconds) -> str:  # type: ignore[no-untyped-def]
        if self.refuse_grant:
            raise BadGatewayError("refused", code="grant_outside_consent")
        self.grants.append(
            {
                "owner": owner,
                "consent": consent_id,
                "ref": ref,
                "items": items,
                "ttl": ttl_seconds,
            }
        )
        return f"grant-{len(self.grants)}"

    async def revoke_test_grant(self, grant_id: str) -> None:
        self.revoked.append(grant_id)

    async def import_item(
        self, *, owner: str, name: str, data: bytes, ref: str
    ) -> dict[str, Any]:
        self.imported.append(ref)
        item_id = f"imported-{ref.rsplit(':', 1)[-1]}"
        self.items[item_id] = data
        return {"item_id": item_id, "sha256": hashlib.sha256(data).hexdigest()}


async def token() -> str:
    return "user-token"


@pytest.fixture
def parts():
    repo, store, kb = MemoryKnowlab(), MemoryDataStore(), FakeKnowledge()
    return KnowbotDataService(repo, store, knowledge=kb), repo, store, kb


async def test_uploads_go_to_knowbase_and_the_test_consent_follows(parts):
    data, repo, store, kb = parts
    first = await data.upload(OWNER, "2023 年报.pdf", PDF, token)
    assert (first.item_id, first.object_key, first.version_id) == ("item-1", None, None)
    assert first.sha256 == hashlib.sha256(PDF).hexdigest() and store.objects == {}
    assert kb.tokens == ["user-token"]
    assert kb.consents == {"consent-1": [{"item_id": "item-1", "sha256": first.sha256}]}
    second = await data.upload(OWNER, "2024 年报.pdf", PDF + b"2", token)
    # 新的同意覆盖两份，旧的撤掉
    assert [i["item_id"] for i in kb.consents["consent-2"]] == ["item-1", "item-2"]
    assert kb.withdrawn == ["consent-1"]
    assert repo.consents[OWNER]["consent_id"] == "consent-2"
    await data.delete(OWNER, str(second.id), token)
    assert kb.deleted == ["item-2"]
    assert [i["item_id"] for i in kb.consents["consent-3"]] == ["item-1"]
    await data.delete(OWNER, str(first.id), token)
    assert OWNER not in repo.consents and kb.withdrawn[-1] == "consent-3"


async def test_opening_the_page_refreshes_a_stale_consent_only(parts):
    data, repo, _, kb = parts
    await data.upload(OWNER, "a.pdf", PDF, token)
    calls = len(kb.consents)
    await data.ensure_consent(OWNER, token)
    assert len(kb.consents) == calls  # 对得上不动
    repo.consents.pop(OWNER)
    await data.ensure_consent(OWNER, token)
    assert len(kb.consents) == calls + 1


async def test_a_failed_withdrawal_stops_the_delete_and_is_retried(parts):
    """撤旧同意不成：删除停下报明，旧同意记在待撤；再删时接着撤（Astra 评审 F03）。"""
    data, repo, _, kb = parts
    found = await data.upload(OWNER, "a.pdf", PDF, token)
    kb.refuse_withdraw = True
    with pytest.raises(ServiceUnavailableError) as refused:
        await data.delete(OWNER, str(found.id), token)
    assert refused.value.code == "consent_not_withdrawn"
    assert kb.deleted == [] and repo.withdrawals == {"consent-1": OWNER}
    assert (await repo.get_dataset(found.id)).deleted_at is None  # type: ignore[union-attr]
    kb.refuse_withdraw = False
    await data.delete(OWNER, str(found.id), token)
    assert kb.deleted == ["item-1"] and kb.withdrawn == ["consent-1"]
    assert repo.withdrawals == {} and OWNER not in repo.consents


async def test_opening_the_page_withdraws_what_is_left(parts):
    data, repo, _, kb = parts
    await data.upload(OWNER, "a.pdf", PDF, token)
    kb.refuse_withdraw = True
    await data.upload(OWNER, "b.pdf", PDF + b"b", token)
    assert repo.withdrawals == {"consent-1": OWNER}
    kb.refuse_withdraw = False
    await data.ensure_consent(OWNER, token)  # 范围对得上，待撤的照样撤
    assert kb.withdrawn == ["consent-1"] and repo.withdrawals == {}


async def test_data_still_read_by_a_grant_keeps_its_place_in_the_consent(parts):
    data, repo, _, kb = parts
    a = await data.upload(OWNER, "a.pdf", PDF, token)
    await data.upload(OWNER, "b.pdf", PDF + b"b", token)
    kb.reading.add(str(a.item_id))
    with pytest.raises(ConcurrencyConflictError):
        await data.delete(OWNER, str(a.id), token)
    assert kb.deleted == []
    current = kb.consents[repo.consents[OWNER]["consent_id"]]
    assert [i["item_id"] for i in current] == ["item-1", "item-2"]  # 放回去了


async def test_bound_data_still_cannot_be_deleted(parts):
    data, repo, _, kb = parts
    found = await data.upload(OWNER, "a.pdf", PDF, token)
    creation = KnowbotCreationService(repo, FakeRuntime())
    request = await accepted_request(creation)
    binding = [{"dataset_id": str(found.id), "version": "1", "sha256": found.sha256}]
    await creation.add_draft(str(request.id), manifest(data_bindings=binding))
    with pytest.raises(ConcurrencyConflictError):
        await data.delete(OWNER, str(found.id), token)
    assert kb.deleted == []


async def test_old_data_moves_once_and_keeps_its_ids(parts):
    repo, store = MemoryKnowlab(), MemoryDataStore()
    legacy = KnowbotDataService(
        repo, store
    )  # 没配 knowbase 时传的：在本应用的对象存储里
    old = await legacy.upload(OWNER, "老年报.pdf", PDF)
    assert old.object_key and old.item_id is None
    kb = FakeKnowledge()
    data = KnowbotDataService(repo, store, knowledge=kb)
    assert await data.import_old() == 1
    moved = await repo.get_dataset(old.id)
    assert moved is not None and moved.item_id == f"imported-{old.id}"
    assert (moved.id, moved.sha256, moved.object_key) == (
        old.id,
        old.sha256,
        old.object_key,
    )
    assert kb.imported == [f"knowlab-dataset:{old.id}"]
    assert await data.import_old() == 0  # 搬过的不再搬
    assert store.deleted == []  # 旧对象留到第 1 版停用


async def test_at_most_a_thousand_files(parts, monkeypatch):
    data, *_ = parts
    monkeypatch.setattr(datasets, "MAX_DATASETS", 1)
    await data.upload(OWNER, "a.pdf", PDF, token)
    with pytest.raises(BadRequestError) as refused:
        await data.upload(OWNER, "b.pdf", PDF + b"b", token)
    assert refused.value.code == "dataset_count_exceeded"


def test_consent_covers_what_is_in_knowbase_in_a_stable_order() -> None:
    import uuid

    from app.application.ports.knowbots import DatasetRecord

    def record(item_id: str | None, sha256: str) -> DatasetRecord:
        return DatasetRecord(
            id=uuid.uuid4(),
            owner_subject=OWNER,
            name="a.pdf",
            version="1",
            content_type="application/pdf",
            size_bytes=1,
            sha256=sha256,
            object_key=None if item_id else "k",
            version_id=None if item_id else "v",
            item_id=item_id,
        )

    a, b, c = record("b", "2" * 64), record("a", "1" * 64), record(None, "3" * 64)
    assert consent_items([a, b, c]) == [
        {"item_id": "a", "sha256": "1" * 64},
        {"item_id": "b", "sha256": "2" * 64},
    ]
    assert items_digest(consent_items([a, b])) == items_digest(consent_items([b, a]))


# ---------- 测试读数凭测试运行授权 ----------


async def _tested(repo, data, kb, *, bound, start=True):  # type: ignore[no-untyped-def]
    runtime = FakeRuntime()
    svc = KnowbotCreationService(repo, runtime, knowledge=kb)
    request = await accepted_request(svc)
    binding = [
        {"dataset_id": str(d.id), "version": "1", "sha256": d.sha256} for d in bound
    ]
    draft = await svc.add_draft(str(request.id), manifest(data_bindings=binding))
    await svc.review_rules(str(draft.id), "admin-1")
    if start:
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    return svc, runtime, draft


async def _start(svc, draft):  # type: ignore[no-untyped-def]
    await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)


async def test_a_test_reads_its_bound_data_under_a_test_grant(parts):
    data, repo, _, kb = parts
    found = await data.upload(OWNER, "a.pdf", PDF, token)
    svc, runtime, draft = await _tested(repo, data, kb, bound=[found])
    _, sent = runtime.started[0]
    assert sent.data_grant is not None and sent.data_grant.grant_id == "grant-1"
    assert [(g.dataset_id, g.item_id) for g in sent.data_grant.items] == [
        (str(found.id), "item-1")
    ]
    assert kb.grants[0]["ref"] == sent.run_id and kb.grants[0]["consent"] == "consent-1"
    assert kb.grants[0]["ttl"] == 600 + sent.max_wait_seconds + 3600
    test = await repo.find_test_by_run(sent.run_id)
    assert test is not None and test.grant_id == "grant-1"  # 授权记在这次测试上
    runtime.results[sent.run_id] = result(sent.run_id, "passed")
    await svc.refresh(str(test.id))
    assert kb.revoked == ["grant-1"]


async def test_without_a_grant_the_test_still_runs_the_old_way(parts):
    data, repo, store, kb = parts
    legacy = KnowbotDataService(repo, store)
    old = await legacy.upload(OWNER, "老年报.pdf", PDF)  # 还没搬
    _, runtime, _ = await _tested(repo, data, kb, bound=[old])
    assert runtime.started[0][1].data_grant is None and kb.grants == []
    # 发出去的请求里没有这一栏（老的运行方不认它）
    assert "data_grant" not in runtime.started[0][1].model_dump(mode="json")


async def test_a_refused_grant_stops_the_test_before_it_runs(parts):
    """签不出测试运行授权就不开跑，不退回旧读路（Astra 评审 F02）。"""
    data, repo, _, kb = parts
    found = await data.upload(OWNER, "a.pdf", PDF, token)
    kb.refuse_grant = True
    svc, runtime, draft = await _tested(repo, data, kb, bound=[found], start=False)
    with pytest.raises(BadGatewayError) as refused:
        await _start(svc, draft)
    assert refused.value.code == "grant_outside_consent"
    assert runtime.started == []
    [test] = await repo.list_tests(request_ids=[draft.request_id])
    assert test.status == "platform_error" and "grant_outside_consent" in test.error
    again = await repo.get_draft(draft.id)
    assert again is not None and again.status == "pending_test"  # 可以再测


async def test_no_consent_or_half_moved_data_is_refused_up_front(parts):
    data, repo, store, kb = parts
    found = await data.upload(OWNER, "a.pdf", PDF, token)
    svc, runtime, draft = await _tested(repo, data, kb, bound=[found], start=False)
    await repo.delete_test_consent(OWNER)
    with pytest.raises(ConcurrencyConflictError) as refused:
        await _start(svc, draft)
    assert refused.value.code == "test_consent_missing"
    old = await KnowbotDataService(repo, store).upload(OWNER, "老.pdf", PDF + b"o")
    svc, _, mixed = await _tested(repo, data, kb, bound=[found, old], start=False)
    with pytest.raises(ConcurrencyConflictError) as refused:
        await _start(svc, mixed)
    assert refused.value.code == "data_not_imported"
    assert runtime.started == [] and kb.grants == []
    for d in (draft, mixed):  # 一次测试也没开
        assert await repo.list_tests(request_ids=[d.request_id]) == []


async def test_a_test_without_bound_data_needs_no_grant(parts):
    data, repo, _, kb = parts
    _, runtime, _ = await _tested(repo, data, kb, bound=[])
    assert runtime.started[0][1].data_grant is None and kb.grants == []


# ---------- 适配器：knowbase 的拒绝转成本应用的码 ----------


class Client:
    def __init__(self, refusal: KnowbaseRefused) -> None:
        self.refusal = refusal

    async def upload(self, *a: Any, **k: Any) -> Any:
        raise self.refusal

    async def delete(self, *a: Any, **k: Any) -> Any:
        raise self.refusal


async def test_knowbase_refusals_become_knowlab_codes() -> None:
    with pytest.raises(BadRequestError) as refused:
        await KnowbaseKnowledgeBase(
            Client(KnowbaseRefused(413, "quota_exceeded"))
        ).upload(  # type: ignore[arg-type]
            "t", "a.pdf", PDF
        )
    assert refused.value.code == "dataset_quota_exceeded"
    with pytest.raises(ConcurrencyConflictError) as in_use:
        await KnowbaseKnowledgeBase(Client(KnowbaseRefused(409, "in_use"))).delete(
            "t", "i"
        )  # type: ignore[arg-type]
    assert in_use.value.code == "dataset_in_use"
    # 已经没有了：当删过了
    await KnowbaseKnowledgeBase(Client(KnowbaseRefused(404, "item_not_found"))).delete(
        "t", "i"
    )  # type: ignore[arg-type]
