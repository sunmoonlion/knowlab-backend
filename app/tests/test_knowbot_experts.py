"""专家与交付（SDD 0037 第五、第六节；SDD 0046 第五节；0043 第 4.2、4.3 节）。

- 平台孵化、平台测的：平台先交回，作者再「提交交付」（先用户、后运营）；
- 自助测的、自助孵化的：作者「提交交付」即过同意门；
- 自用轻审能自动查的自动查，都过就进 knowmart，查不过才交人；公开完整审核，自助测的
  还要平台重跑（作者的账号、作者的电脑）；审核员看结果、不看原始数据；
- 交审时作者授权审核员（与重跑），审完撤销；所绑数据交付时冻结、许可预查；自用转公开
  重查许可；类目由平台人员选、交付带过去。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import TestSetRecord
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_deliveries import KnowbotDeliveryService
from app.application.services.knowbot_experts import KnowbotExpertService, stage_of
from app.application.services.knowbot_grants import self_acceptance
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.delivery import (
    AUTO_REVIEWER,
    DeliveryReviewIn,
    DeliverySubmitIn,
    auto_check,
)
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.test_run import CaseOutputsV1
from tests.knowbot_fakes import (
    COMPUTER,
    COMPUTER_ROOT,
    OWNER,
    FakeKnowbaseDelivery,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
    manifest,
    own_computer,
    platform_draft,
    request_body,
    result,
    user_token,
)

NOW = datetime(2026, 10, 24, 9, tzinfo=UTC)
SELF = DeliverySubmitIn(listing="self", authorize_reviewer=True)
PUBLIC = DeliverySubmitIn(
    listing="public", authorize_reviewer=True, budget_limit=Decimal("5")
)
BOUND = {
    "material_id": "m-bound",
    "version_no": 2,
    "sha256": "b" * 64,
    "license": "via_expert",
}


class Parts:
    def __init__(self) -> None:
        self.repo = MemoryKnowlab()
        self.runtime = FakeRuntime()
        self.knowmart = FakeKnowmart()
        self.knowbase = FakeKnowbaseDelivery()
        own_computer(self.repo)  # 平台测试在作者的电脑上跑（decisions ⑯）
        self.creation = KnowbotCreationService(
            self.repo, self.runtime, now=lambda: NOW, knowbase=self.knowbase
        )
        self.experts = KnowbotExpertService(
            self.repo,
            self.knowmart,
            now=lambda: NOW,
            knowbase=self.knowbase,
            creation=self.creation,
        )
        self.deliveries = KnowbotDeliveryService(
            self.repo,
            self.knowmart,
            self.knowbase,
            self.creation,
            self.experts,
            now=lambda: NOW,
        )

    def clean_outputs(self, run_id: str, text: str = "毛利率 52%，比去年高") -> None:
        self.runtime.case_outputs[(run_id, "c1")] = CaseOutputsV1.model_validate(
            {
                "contract": "knowbot-test-run/v1",
                "run_id": run_id,
                "case_id": "c1",
                "deliverable": {"path": "out/report.md", "text": text},
            }
        )

    async def platform_expert(self):
        """平台孵化、平台测通过的一版。"""
        request = await self.creation.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
        await self.creation.accept(str(request.id), 1, all_clear(), "admin-1")
        draft = await platform_draft(self.creation, str(request.id), manifest())
        await self.creation.review_rules(str(draft.id), "admin-1")
        test = await self.creation.start_test(
            str(draft.id), budget_limit="20", timeout_seconds=600
        )
        self.clean_outputs(test.run_id)
        self.runtime.results[test.run_id] = result(test.run_id, "passed")
        await self.creation.refresh(str(test.id))
        [expert] = await self.repo.list_experts([request.id])
        return request, expert, test

    async def self_expert(self, text: str = "毛利率 52%，比去年高"):
        """自助孵化、自助测通过的一版。"""
        request = await self.creation.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
        async with self.repo.transaction():
            draft = await self.repo.create_draft(
                request_id=request.id,
                request_version=1,
                revision=1,
                version="1",
                manifest=manifest(),
                mode="self",
            )
            await self.repo.update_draft(draft.id, rules_reviewed_by="program")
            await self.repo.add_acceptance(self_acceptance(request.id, 1, NOW))
        test = await self.creation.start_self_test(
            OWNER,
            str(draft.id),
            environment_id=COMPUTER,
            workspace_root=COMPUTER_ROOT,
            budget_limit="5",
            timeout_seconds=600,
        )
        self.clean_outputs(test.run_id, text)
        self.runtime.results[test.run_id] = result(test.run_id, "passed")
        await self.creation.refresh(str(test.id))
        [expert] = await self.repo.list_experts([request.id])
        return request, expert, test


@pytest.fixture
def p() -> Parts:
    return Parts()


@pytest.mark.asyncio
async def test_platform_made_and_tested_is_returned_first_then_submitted(p):
    request, expert, _ = await p.platform_expert()
    eid = str(expert.id)
    assert expert.needs_return and stage_of(expert) == "to_return"
    with pytest.raises(NotFoundError):  # 没交回的作者看不到
        await p.deliveries.submit(OWNER, eid, SELF, user_token)
    await p.experts.return_to_user(eid, "ops-1")
    p.knowbase.bound = [dict(BOUND)]
    with pytest.raises(NotFoundError):
        await p.deliveries.submit("someone-else", eid, SELF, user_token)
    done = await p.deliveries.submit(OWNER, eid, SELF, user_token)
    # 自用轻审：全过，自动审过、马上交 knowmart
    assert done.status == "approved" and done.decided_by == AUTO_REVIEWER
    assert done.auto_check == {
        "passed": True,
        "findings": [],
        "checked_at": NOW.isoformat(),
    }
    listed = await p.repo.get_expert(expert.id)
    assert stage_of(listed) == "listed"
    assert (listed.user_accepted, listed.user_decided_by) == (True, OWNER)
    assert (listed.review_listing, listed.reviewed_by) == ("self", AUTO_REVIEWER)
    # 名录推了、绑定冻结在这一版，清单 v2 带着冻结的绑定交过去（不带许可那一栏）
    assert p.knowbase.directory[str(request.id)]["name"] == "财务分析与预测"
    assert p.knowbase.frozen == [(str(request.id), "1", OWNER)]
    assert p.knowmart.delivered == [
        {
            "delivery_id": f"knowlab-delivery-{done.id}",
            "owner_subject": OWNER,
            "knowbot_ref": f"knowlab-{request.id}",
            "manifest": {
                **manifest(),
                "bindings": [
                    {
                        "material_id": "m-bound",
                        "version_no": 2,
                        "sha256": "b" * 64,
                        "basis_ref": f"expert:{request.id}@1",
                    }
                ],
            },
            "request_public": False,
        }
    ]
    # 作者授权审核员看这次交付（绑定的那几份），审完撤销
    [consent] = p.knowbase.consents
    assert (consent["kind"], consent["ref"]) == ("review", str(done.id))
    assert consent["items"] == [{"material_id": "m-bound", "version_no": 2}]
    assert p.knowbase.ended == [
        {"subject": OWNER, "kind": "review", "ref": str(done.id), "reason": "finished"}
    ]
    assert done.consent_ended_at == NOW
    with pytest.raises(ConcurrencyConflictError):  # 交过的不再交
        await p.deliveries.submit(OWNER, eid, SELF, user_token)


@pytest.mark.asyncio
async def test_self_tested_is_submitted_at_once_and_flags_go_to_a_person(p):
    _, expert, _ = await p.self_expert("建议买入，目标价 2000 元")
    assert not expert.needs_return and stage_of(expert) == "awaiting_user"
    found = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    # 自动查不过：交平台人员看（只记哪条用例、哪个词，不带原文）
    # 没绑数据也留了一条审核授权（作者在 knowbase 撤了查得到）
    assert found.status == "reviewing" and found.consents.get("review") == 1
    words = {f["word"] for f in found.auto_check["findings"]}
    assert words == {"建议买入", "目标价"}
    assert p.knowmart.delivered == []
    assert stage_of(await p.repo.get_expert(expert.id)) == "to_review"
    with pytest.raises(ValueError):
        DeliveryReviewIn(approved=False)
    rejected = await p.deliveries.review(
        str(found.id),
        DeliveryReviewIn(approved=False, reason="成稿里有目标价（C-L5）"),
        "ops-1",
    )
    assert rejected.status == "rejected"
    stored = await p.repo.get_expert(expert.id)
    assert stage_of(stored) == "rejected"
    assert stored.review_reason == "成稿里有目标价（C-L5）"
    assert p.knowmart.unlisted[-1]["outcome"] == "rejected"
    with pytest.raises(ConcurrencyConflictError):
        await p.deliveries.review(
            str(found.id), DeliveryReviewIn(approved=True), "ops-1"
        )


@pytest.mark.asyncio
async def test_bindings_are_prechecked_against_the_listing(p):
    _, expert, _ = await p.self_expert()
    p.knowbase.bound = [dict(BOUND, license="self")]
    p.knowbase.states["m-bound"] = {
        "state": "live",
        "license": "self",
        "shareable": False,
    }
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    assert caught.value.code == "binding_not_shareable"
    assert p.repo.deliveries == {}
    # 只许本人用的交到自用可以
    done = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    assert done.status == "approved"
    assert done.binding_check["items"][0]["license"] == "self"


@pytest.mark.asyncio
async def test_public_self_tested_needs_a_platform_rerun_on_the_authors_computer(p):
    request, expert, test = await p.self_expert()
    eid = str(expert.id)
    # 作者那次自助测试确认过的测试集：自己的一份、公开原件一份
    p.repo.test_sets.append(
        TestSetRecord(
            id=uuid.uuid4(),
            request_id=request.id,
            version=1,
            cases={
                "c1": {
                    "items": [
                        {
                            "material_id": "m-mine",
                            "version_no": 1,
                            "sha256": "a" * 64,
                            "basis": "display",
                            "basis_ref": "app:knowlab",
                            "name": "我的表.csv",
                            "media_type": "text/csv",
                            "size_bytes": 10,
                        },
                        {
                            "material_id": "m-public",
                            "version_no": 3,
                            "sha256": "c" * 64,
                            "basis": "public",
                            "basis_ref": "public:disclosure",
                            "name": "年报.pdf",
                            "media_type": "application/pdf",
                            "size_bytes": 20,
                        },
                    ]
                }
            },
            created_at=NOW,
            from_test_id=test.id,
        )
    )
    with pytest.raises(BadRequestError):  # 公开、自助测的：作者要设重跑的费用上限
        await p.deliveries.submit(
            OWNER,
            eid,
            DeliverySubmitIn(listing="public", authorize_reviewer=True),
            user_token,
        )
    found = await p.deliveries.submit(OWNER, eid, PUBLIC, user_token)
    assert found.status == "reviewing" and found.needs_rerun
    # 重跑要读的冻结输入：只有作者自己展示给 knowlab 的那一份要作者授权
    [rerun_consent] = [c for c in p.knowbase.consents if c["kind"] == "rerun"]
    assert rerun_consent["items"] == [{"material_id": "m-mine", "version_no": 1}]
    with pytest.raises(ConcurrencyConflictError) as caught:  # 没重跑不能过
        await p.deliveries.review(
            str(found.id),
            DeliveryReviewIn(approved=True, category="tools.lookup"),
            "ops-1",
        )
    assert caught.value.code == "rerun_not_passed"
    rerun = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert rerun.mode == "rerun" and rerun.started_by == "ops-1"
    _, run = p.runtime.started[-1]
    # 在作者的账号下、作者那台电脑那个目录、作者设的上限（decisions ⑯）
    assert run.owner_subject == OWNER
    assert (run.runner.environment_id, run.runner.workspace_root) == (
        COMPUTER,
        COMPUTER_ROOT,
    )
    assert run.budget.limit == "5.00"
    # 另签机器授权：自己的那份凭重跑依据，公开原件照旧凭公开
    [grant] = p.knowbase.grants
    assert [(i["basis"], i["basis_ref"]) for i in grant["items"]] == [
        ("platform", f"platform:knowlab:rerun:{found.id}"),
        ("public", "public:disclosure"),
    ]
    with pytest.raises(ConcurrencyConflictError):  # 在跑的不再发起
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    p.runtime.results[rerun.run_id] = result(rerun.run_id, "passed")
    await p.creation.refresh(str(rerun.id))
    # 重跑不出专家、不动准专家，审核员看得到结论
    assert len(await p.repo.list_experts([request.id])) == 1
    with pytest.raises(BadRequestError):  # 公开要选类目
        await p.deliveries.review(
            str(found.id), DeliveryReviewIn(approved=True), "ops-1"
        )
    done = await p.deliveries.review(
        str(found.id),
        DeliveryReviewIn(approved=True, category="tools.lookup"),
        "ops-1",
    )
    assert done.status == "approved" and done.category == "tools.lookup"
    delivered = p.knowmart.delivered[-1]
    assert delivered["request_public"] is True
    assert delivered["category"] == "tools.lookup"
    assert stage_of(await p.repo.get_expert(expert.id)) == "listed"
    # 审核、重跑两种依据都留了（没绑数据记账号级的一条），审完撤
    assert {e["kind"] for e in p.knowbase.ended} == {"review", "rerun"}


@pytest.mark.asyncio
async def test_self_listing_turns_public_after_a_recheck_and_full_review(p):
    _, expert, _ = await p.platform_expert()
    eid = str(expert.id)
    await p.experts.return_to_user(eid, "ops-1")
    p.knowbase.bound = [dict(BOUND)]
    first = await p.deliveries.submit(OWNER, eid, SELF, user_token)
    assert first.status == "approved"
    # 平台测的公开不用重跑；同一版、沿用上一次冻结的绑定
    p.knowbase.states["m-bound"] = {
        "state": "live",
        "license": "self",
        "shareable": False,
    }
    with pytest.raises(ConcurrencyConflictError) as caught:  # 自用转公开重查许可
        await p.deliveries.submit(OWNER, eid, PUBLIC, user_token)
    assert caught.value.code == "binding_not_shareable"
    p.knowbase.states.clear()
    turned = await p.deliveries.submit(OWNER, eid, PUBLIC, user_token)
    assert turned.conversion and not turned.needs_rerun
    assert turned.status == "reviewing"
    assert p.knowbase.frozen == [
        (str(p.repo.experts[expert.id].request_id), "1", OWNER)
    ]
    p.knowmart.fail = True
    kept = await p.deliveries.review(
        str(turned.id),
        DeliveryReviewIn(approved=True, category="finance.securities_advisory"),
        "ops-1",
    )
    assert kept.status == "approved" and kept.delivered_at is None
    assert kept.delivery_error == "delivery failed: knowmart_unavailable"
    with pytest.raises(AppException):
        await p.deliveries.retry(str(turned.id))
    p.knowmart.fail = False
    done = await p.deliveries.retry(str(turned.id))
    assert done.delivered_at == NOW
    sent = p.knowmart.delivered[-1]
    assert sent["delivery_id"] == f"knowlab-delivery-{turned.id}"
    assert sent["manifest"] == p.knowmart.delivered[0]["manifest"]  # 同一版同一份
    assert sent["request_public"] is True
    assert (await p.repo.get_expert(expert.id)).review_listing == "public"


@pytest.mark.asyncio
async def test_consents_left_open_are_closed_later(p):
    _, expert, _ = await p.self_expert()
    p.knowbase.bound = [dict(BOUND)]
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    # 不过；knowbase 那时连不上，撤销由后台补做
    p.knowbase.fail = True
    rejected = await p.deliveries.review(
        str(found.id), DeliveryReviewIn(approved=False, reason="方法写得不清"), "ops-1"
    )
    assert rejected.consent_ended_at is None
    p.knowbase.fail = False
    assert await p.deliveries.close_pending() == 1
    assert (await p.repo.get_delivery(found.id)).consent_ended_at == NOW
    assert await p.deliveries.close_pending() == 0


@pytest.mark.asyncio
async def test_the_user_may_decline_and_nothing_is_listed(p):
    request, expert, _ = await p.platform_expert()
    await p.experts.return_to_user(str(expert.id), "ops-1")
    p.knowmart.fail = True  # 交不到不挡作者的决定
    declined = await p.experts.decline(OWNER, str(expert.id))
    assert stage_of(declined) == "declined" and p.knowmart.unlisted == []
    with pytest.raises(ConcurrencyConflictError):
        await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    assert await p.experts.resend_unlisted() == 0
    p.knowmart.fail = False
    assert await p.experts.resend_unlisted() == 1
    assert p.knowmart.unlisted == [
        {
            "owner_subject": OWNER,
            "knowbot_ref": f"knowlab-{request.id}",
            "version": "1",
            "outcome": "declined",
        }
    ]
    assert await p.experts.resend_unlisted() == 0


@pytest.mark.asyncio
async def test_stopping_stops_knowmart_first_by_the_user_or_by_us(p):
    _, expert, _ = await p.self_expert()
    eid = str(expert.id)
    with pytest.raises(ConcurrencyConflictError):  # 没上架的停不了
        await p.experts.stop(eid, "不要了", "ops-1")
    await p.deliveries.submit(OWNER, eid, SELF, user_token)
    with pytest.raises(NotFoundError):
        await p.experts.stop(eid, "不要了", "x", owner_subject="someone-else")
    p.knowmart.fail = True
    with pytest.raises(AppException):  # knowmart 没停成，这边不记
        await p.experts.stop(eid, "不要了", OWNER, owner_subject=OWNER)
    p.knowmart.fail = False
    stopped = await p.experts.stop(eid, "不要了", OWNER, owner_subject=OWNER)
    assert stage_of(stopped) == "stopped"
    assert p.knowmart.stopped == [
        {"owner_subject": OWNER, "knowbot_id": "kb-1", "version": "1"}
    ]


@pytest.mark.asyncio
async def test_test_evidence_goes_into_the_output_zone_and_directory_syncs(p):
    request, expert, test = await p.self_expert()
    stored = await p.repo.get_test(test.id)
    assert stored.evidence == {"material_id": "out-1", "version_no": 1}
    [written] = p.knowbase.outputs
    assert (written["beneficiary"], written["task_ref"], written["run_ref"]) == (
        OWNER,
        f"knowlab-test:{test.id}",
        test.run_id,
    )
    assert written["expert_id"] == str(request.id)
    body = json.loads(written["content"])
    assert body["cases"][0]["verdict"] == "passed"
    assert "deliverable" not in written["content"].decode()  # 不带成稿
    # 写不成的记次数、往后排，不每分钟排在最前；到了时候再写
    p.knowbase.fail_outputs = True
    await p.repo.update_test(test.id, evidence=None)
    assert await p.creation.write_pending_evidence() == 0
    failed = await p.repo.get_test(test.id)
    assert (
        failed.evidence["failures"] == 1 and failed.evidence["code"] == "quota_exceeded"
    )
    p.knowbase.fail_outputs = False
    assert await p.creation.write_pending_evidence() == 0  # 还没到时候
    p.creation.now = lambda: NOW + timedelta(minutes=3)
    assert await p.creation.write_pending_evidence() == 1
    # 没有报告的记一笔出队，不每分钟再取
    await p.repo.update_test(test.id, evidence=None, report=None)
    assert await p.creation.write_pending_evidence() == 0
    assert (await p.repo.get_test(test.id)).evidence == {"skipped": "no_report"}
    assert await p.repo.tests_without_evidence(NOW + timedelta(days=9)) == []
    # 名录：名称与最新版本变了才推
    assert await p.deliveries.sync_directory() == 1
    assert p.knowbase.directory[str(request.id)] == {
        "owner": OWNER,
        "name": "财务分析与预测",
        "latest_version": "1.1",
    }
    assert await p.deliveries.sync_directory() == 0
    assert stage_of(expert) == "awaiting_user"


# —— 交付审查 10-30 ——


@pytest.mark.asyncio
async def test_precheck_freezes_nothing_so_the_author_can_fix_and_resubmit(p):
    """第一次交公开被许可挡回（只许本人用）：预查不冻结；作者改了许可再交，冻结的就是
    改后的那一档（原来第一次就冻结了，再交回的还是旧版本旧许可，买家请专家对不上）。"""
    request, expert, _ = await p.self_expert()
    p.knowbase.bound = [dict(BOUND)]
    p.knowbase.states["m-bound"] = {"state": "live", "license": "self"}
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    assert caught.value.code == "binding_not_shareable"
    assert p.knowbase.frozen == [] and p.knowbase.consents == []
    p.knowbase.states["m-bound"] = {"state": "live", "license": "via_expert"}
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    assert [b["license"] for b in found.bindings] == ["via_expert"]
    assert found.binding_check["items"][0]["frozen_license"] == "via_expert"


@pytest.mark.asyncio
async def test_frozen_for_own_use_cannot_turn_public_without_a_new_version(p):
    """冻结时只许本人用：自用转公开时冻结的那一档改不了（签授权看它），要出新一版。"""
    _, expert, _ = await p.platform_expert()
    await p.experts.return_to_user(str(expert.id), "ops-1")
    p.knowbase.bound = [dict(BOUND, license="self")]
    p.knowbase.states["m-bound"] = {"state": "live", "license": "self"}
    assert (
        await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    ).status == ("approved")
    p.knowbase.states["m-bound"] = {"state": "live", "license": "via_expert"}
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    assert caught.value.code == "binding_frozen_self_only"


@pytest.mark.asyncio
async def test_a_rerun_of_public_originals_only_still_gets_its_grant(p):
    """用例数据全是公开原件（「财务分析与预测」就是）：重跑的机器授权照交付过程记，
    签得下（knowbase 的真规则见 knowbase 的测试）。"""
    request, expert, test = await p.self_expert()
    p.repo.test_sets.append(
        TestSetRecord(
            id=uuid.uuid4(),
            request_id=request.id,
            version=1,
            cases={
                "c1": {
                    "items": [
                        {
                            "material_id": "m-public",
                            "version_no": 3,
                            "sha256": "c" * 64,
                            "basis": "public",
                            "basis_ref": "public:disclosure",
                            "name": "年报.pdf",
                            "media_type": "application/pdf",
                            "size_bytes": 20,
                        }
                    ]
                }
            },
            created_at=NOW,
            from_test_id=test.id,
        )
    )
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    # 没有要作者授权的资料：两种依据各记一条账号级的，重跑凭这次过程的同意签授权
    assert [(c["kind"], c["items"]) for c in p.knowbase.consents] == [
        ("review", []),
        ("rerun", []),
    ]
    await p.deliveries.start_rerun(str(found.id), "ops-1")
    [grant] = p.knowbase.grants
    assert grant["platform_use"] == f"platform:knowlab:rerun:{found.id}"
    assert [i["basis"] for i in grant["items"]] == ["public"]


@pytest.mark.asyncio
async def test_a_failed_rerun_cannot_be_started_again_only_a_platform_error(p):
    _, expert, _ = await p.self_expert()
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    first = await p.deliveries.start_rerun(str(found.id), "ops-1")
    p.runtime.results[first.run_id] = result(first.run_id, "failed")
    await p.creation.refresh(str(first.id))
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert caught.value.code == "rerun_exists"
    # 平台出错的可以再发起
    await p.repo.update_test(
        first.id,
        status="platform_error",
        report={"cost": {"spent": "1", "currency": "CNY"}},
    )
    again = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert again.id != first.id


@pytest.mark.asyncio
async def test_the_author_withdraws_and_the_running_rerun_stops(p):
    request, expert, _ = await p.self_expert()
    p.knowbase.bound = [dict(BOUND)]
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    rerun = await p.deliveries.start_rerun(str(found.id), "ops-1")
    p.runtime.can_cancel = False  # 运行方还没有停的接口：照样撤授权、记平台出错
    with pytest.raises(NotFoundError):
        await p.deliveries.withdraw("someone-else", str(expert.id))
    done = await p.deliveries.withdraw(OWNER, str(expert.id))
    assert (done.status, done.withdrawn_by) == ("withdrawn", OWNER)
    assert p.runtime.cancelled == [rerun.run_id]
    assert (await p.repo.get_test(rerun.id)).status == "platform_error"
    assert {(e["kind"], e["reason"]) for e in p.knowbase.ended} == {
        ("review", "cancelled"),
        ("rerun", "cancelled"),
    }
    # 没交到 knowmart 的那次冻结一并作废（决定 10-30）
    assert p.knowbase.voided == [f"expert:{request.id}@1"]
    back = await p.repo.get_expert(expert.id)
    assert stage_of(back) == "awaiting_user" and back.user_accepted is None
    # 撤回了可以再交（冻结作废了，照现在的绑定重冻）
    again = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    assert again.status == "approved"
    assert len(p.knowbase.frozen) == 2


@pytest.mark.asyncio
async def test_a_rejection_stops_the_running_rerun_first(p):
    _, expert, _ = await p.self_expert()
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    rerun = await p.deliveries.start_rerun(str(found.id), "ops-1")
    await p.deliveries.review(
        str(found.id), DeliveryReviewIn(approved=False, reason="方法不清"), "ops-1"
    )
    assert p.runtime.cancelled == [rerun.run_id]
    assert (await p.repo.get_test(rerun.id)).status == "platform_error"


@pytest.mark.asyncio
async def test_an_approval_knowmart_keeps_refusing_can_be_voided(p):
    _, expert, _ = await p.self_expert()
    p.knowmart.fail = True
    found = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    assert found.status == "approved" and found.delivered_at is None
    with pytest.raises(ConcurrencyConflictError):  # 还挡着：不能再交
        await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    with pytest.raises(AppException):  # knowmart 连不上：问不到收没收下，不作废
        await p.deliveries.void(str(found.id), "ops-1", "作者改低了许可")
    p.knowmart.fail = False
    voided = await p.deliveries.void(str(found.id), "ops-1", "作者改低了许可")
    assert (voided.status, voided.withdraw_reason) == ("withdrawn", "作者改低了许可")
    assert stage_of(await p.repo.get_expert(expert.id)) == "awaiting_user"


@pytest.mark.asyncio
async def test_a_submit_that_fails_halfway_leaves_no_consent_behind(p):
    """review 依据留了、rerun 依据被拒：整段失败，按这次的编号两种都撤，交付不建。"""
    request, expert, test = await p.self_expert()
    p.knowbase.bound = [dict(BOUND)]
    p.repo.test_sets.append(
        TestSetRecord(
            id=uuid.uuid4(),
            request_id=request.id,
            version=1,
            cases={
                "c1": {
                    "items": [
                        {
                            "material_id": "m-mine",
                            "version_no": 1,
                            "sha256": "a" * 64,
                            "basis": "display",
                            "basis_ref": "app:knowlab",
                            "name": "我的表.csv",
                            "media_type": "text/csv",
                            "size_bytes": 10,
                        }
                    ]
                }
            },
            created_at=NOW,
            from_test_id=test.id,
        )
    )
    p.knowbase.fail_consent_kind = "rerun"
    with pytest.raises(AppException):
        await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    [review] = p.knowbase.consents
    assert {(e["kind"], e["ref"]) for e in p.knowbase.ended} == {
        ("review", review["ref"]),
        ("rerun", review["ref"]),
    }
    assert p.repo.deliveries == {}
    assert stage_of(await p.repo.get_expert(expert.id)) == "awaiting_user"


@pytest.mark.asyncio
async def test_operators_lose_the_manifest_and_tests_once_the_review_ends(p):
    """审完（过、不过、撤回）运营只留摘要：看不到清单（准专家）与测试，交付不带绑定。"""
    _, expert, _ = await p.self_expert("建议买入")
    p.knowbase.bound = [dict(BOUND)]
    found = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    board = KnowbotBoard(p.repo, now=lambda: NOW)
    [life] = await board.lives(operator=True)
    assert [d.id for d in life.drafts] == [expert.draft_id] and life.tests
    await p.deliveries.review(
        str(found.id), DeliveryReviewIn(approved=False, reason="有建议买入"), "ops-1"
    )
    # 不过、没上架：这一版的清单、测试、专家都不再给（这个名称请了平台孵化，名称
    # 本身照孵化授权看得到）
    [life] = await board.lives(operator=True)
    assert (life.drafts, life.tests, life.experts) == ((), (), ())
    with pytest.raises(NotFoundError):
        await p.creation.operator_draft(str(expert.draft_id))


@pytest.mark.parametrize(
    "text",
    [
        "买入返售金融资产 12.3 亿元，较上年增加 3%",
        "卖出回购金融资产款下降",
        "控股股东增持 1% 股份；另一股东披露减持计划",
        "营业收入翻倍",
        "毛利率 52%，比去年高",
    ],
)
def test_subjects_and_facts_are_not_investment_advice(text):
    """财务科目与事实陈述不算投资建议（10-30 交付审查第 11 条）。"""
    found = auto_check(
        report={"cases": [{"case_id": "c1", "status": "passed"}]},
        test_status="passed",
        accepted=True,
        case_ids=["c1"],
        outputs={"c1": {"deliverable": {"text": text}}},
    )
    assert found == []


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("建议买入，目标价 2000 元", "建议买卖"),
        ("给予“买入”评级", "评级"),
        ("维持增持评级", "评级"),
        ("目标价 2000 元", "目标价"),
        ("强烈推荐", "荐股"),
        ("保证收益 8%", "收益承诺"),
    ],
)
def test_investment_advice_is_caught_by_its_wording(text, rule):
    found = auto_check(
        report={"cases": [{"case_id": "c1", "status": "passed"}]},
        test_status="passed",
        accepted=True,
        case_ids=["c1"],
        outputs={"c1": {"deliverable": {"text": text}}},
    )
    assert rule in {f.get("rule") for f in found}


@pytest.mark.asyncio
async def test_the_directory_pushes_only_names_with_drafts_and_knowmart_state_shows(p):
    request, expert, _ = await p.self_expert()
    await p.creation.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    assert await p.deliveries.sync_directory() == 1  # 没出过准专家的那个不推
    assert list(p.knowbase.directory) == [str(request.id)]
    assert await p.deliveries.sync_directory() == 0
    # knowmart 那头转待处理：后台取回，专家上记着原因
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    p.knowmart.version_states = [
        {
            "version": "1",
            "status": "held",
            "held_reason": "binding_deleted",
            "public_allowed": False,
        }
    ]
    assert await p.experts.sync_market() == 1
    stored = await p.repo.get_expert(expert.id)
    assert (stored.delivery["version_status"], stored.delivery["held_reason"]) == (
        "held",
        "binding_deleted",
    )
    assert await p.experts.sync_market() == 0


@pytest.mark.asyncio
async def test_a_review_consent_withdrawn_in_knowbase_counts_as_the_author_withdrawing(
    p,
):
    """作者在 knowbase 总栏撤了审核授权：后台查到，照作者撤回处理——停重跑、撤授权、
    交付记撤回（撤回人是作者）、运营看不到清单与测试（决定 10-30）。"""
    _, expert, _ = await p.self_expert()
    p.knowbase.bound = [dict(BOUND)]
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    rerun = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert await p.deliveries.check_consents() == 0  # 还在：不动
    p.knowbase.withdrawn_in_knowbase.add(str(found.id))
    p.knowbase.fail = True  # knowbase 连不上：这一轮不动
    assert await p.deliveries.check_consents() == 0
    p.knowbase.fail = False
    assert await p.deliveries.check_consents() == 1
    done = await p.repo.get_delivery(found.id)
    assert (done.status, done.withdrawn_by, done.withdraw_reason) == (
        "withdrawn",
        OWNER,
        "作者在 knowbase 撤了审核授权",
    )
    assert p.runtime.cancelled == [rerun.run_id]
    assert (await p.repo.get_test(rerun.id)).status == "platform_error"
    assert stage_of(await p.repo.get_expert(expert.id)) == "awaiting_user"
    board = KnowbotBoard(p.repo, now=lambda: NOW)
    [life] = await board.lives(operator=True)
    assert life.drafts == () and life.tests == ()
    assert await p.deliveries.check_consents() == 0  # 处理过的不再查


# —— 交付审查第二轮（10-30）——


def _public_set(request, test, *, cases=("c1",)):
    return TestSetRecord(
        id=uuid.uuid4(),
        request_id=request.id,
        version=1,
        cases={
            c: {
                "items": [
                    {
                        "material_id": f"m-{c}",
                        "version_no": 1,
                        "sha256": "a" * 64,
                        "basis": "display",
                        "basis_ref": "app:knowlab",
                        "name": f"{c}.csv",
                        "media_type": "text/csv",
                        "size_bytes": 10,
                    }
                ]
            }
            for c in cases
        },
        created_at=NOW,
        from_test_id=test.id,
    )


@pytest.mark.asyncio
async def test_market_state_and_consent_checks_rotate_through_everyone(p):
    """每轮只取 limit 位，下一轮接着取没取过的（原来总是前 100 位）。"""
    first, e1, _ = await p.self_expert()
    second, e2, _ = await p.self_expert()
    for e in (e1, e2):
        await p.deliveries.submit(OWNER, str(e.id), SELF, user_token)
    p.knowmart.version_states = [
        {
            "version": "1",
            "status": "active",
            "held_reason": None,
            "public_allowed": False,
        }
    ]
    await p.experts.sync_market(limit=1)
    p.experts.now = lambda: NOW + timedelta(minutes=1)
    await p.experts.sync_market(limit=1)
    checked = [
        (await p.repo.get_expert(e.id)).delivery.get("market_checked_at")
        for e in (e1, e2)
    ]
    assert all(checked) and checked[0] != checked[1]


@pytest.mark.asyncio
async def test_a_just_approved_delivery_is_not_taken_for_a_withdrawal(p):
    """后台拿着「在审」的旧快照去撤，锁里看到已经审过了就不动（原来会被当成作者撤回）。"""
    _, expert, _ = await p.self_expert("建议买入")
    stale = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    await p.deliveries.review(str(stale.id), DeliveryReviewIn(approved=True), "ops-1")
    kept = await p.deliveries._withdraw(
        stale, by=OWNER, reason="作者在 knowbase 撤了审核授权", background=True
    )
    assert kept.status == "approved" and kept.delivered_at is not None


@pytest.mark.asyncio
async def test_a_withdrawal_landing_while_knowmart_takes_it_stops_that_version(p):
    """交 knowmart 的同时作者撤回（插桩在交的那一刻撤）：写结果前加锁核状态，已撤回的去
    knowmart 停那一版，不记上架。"""
    _, expert, _ = await p.self_expert()
    p.knowmart.fail = True
    found = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    assert found.status == "approved" and found.delivered_at is None
    p.knowmart.fail = False

    async def withdraw_meanwhile():
        p.knowmart.on_deliver = None
        await p.deliveries._withdraw(found, by=OWNER, reason="作者撤回")

    p.knowmart.on_deliver = withdraw_meanwhile
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.retry(str(found.id))
    assert caught.value.code == "delivery_state"
    assert p.knowmart.stopped[-1]["version"] == "1"
    assert (await p.repo.get_delivery(found.id)).status == "withdrawn"
    after = await p.repo.get_expert(expert.id)
    assert after.listed_at is None and after.stopped_at is not None
    assert stage_of(after) == "stopped"


@pytest.mark.asyncio
async def test_withdrawing_an_approval_knowmart_already_took_is_refused(p):
    """回执丢了而 knowmart 其实收下了：撤回、作废前先问 knowmart，收下了的不许撤（请重试
    交付把交成补记上）；单号带交付编号。"""
    _, expert, _ = await p.self_expert()
    p.knowmart.fail = True
    found = await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    p.knowmart.fail = False
    p.knowmart.version_states = [
        {
            "version": "1",
            "status": "active",
            "held_reason": None,
            "public_allowed": False,
        }
    ]
    for act in (
        p.deliveries.withdraw(OWNER, str(expert.id)),
        p.deliveries.void(str(found.id), "ops-1", "交不成"),
    ):
        with pytest.raises(ConcurrencyConflictError) as caught:
            await act
        assert caught.value.code == "delivered_in_knowmart"
    done = await p.deliveries.retry(str(found.id))
    assert done.delivered_at is not None
    assert p.knowmart.delivered[-1]["delivery_id"] == f"knowlab-delivery-{found.id}"


@pytest.mark.asyncio
async def test_operators_do_not_see_a_self_service_authors_origin(p):
    """纯自助作者的来源、参考回答运营看不到（只给孵化授权在效的平台孵化）。"""
    request, expert, _ = await p.self_expert()
    p.repo.requests[request.id] = replace(
        p.repo.requests[request.id],
        origin={"app": "knowbench", "task_id": "t-1"},
        reference_answer="我的模型直接答的",
    )
    for g in list(p.repo.grants.values()):
        p.repo.grants[g.id] = replace(g, status="revoked")
    await p.deliveries.submit(
        OWNER,
        str(expert.id),
        DeliverySubmitIn(listing="self", authorize_reviewer=True),
        user_token,
    )
    board = KnowbotBoard(p.repo, now=lambda: NOW)
    [life] = await board.lives(operator=True)
    assert (life.request.origin, life.request.reference_answer) == (None, None)


@pytest.mark.asyncio
async def test_a_frozen_own_use_licence_can_be_raised_when_turning_public(p):
    """自用转公开：冻结时只许本人用的，作者勾「我有权」往高改（knowbase 核现在的许可
    够、留痕），清单不变、不用出新一版（决定 10-30）。"""
    _, expert, _ = await p.platform_expert()
    await p.experts.return_to_user(str(expert.id), "ops-1")
    p.knowbase.bound = [dict(BOUND, license="self")]
    p.knowbase.states["m-bound"] = {"state": "live", "license": "self"}
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    p.knowbase.states["m-bound"] = {"state": "live", "license": "via_expert"}
    raised = DeliverySubmitIn(
        listing="public", authorize_reviewer=True, raise_frozen=True
    )
    turned = await p.deliveries.submit(OWNER, str(expert.id), raised, user_token)
    assert turned.status == "reviewing" and turned.binding_check["problem"] is None
    assert p.knowbase.raised == [
        (f"expert:{expert.request_id}@1", "m-bound", "via_expert")
    ]
    assert [b["license"] for b in turned.bindings] == ["via_expert"]


@pytest.mark.asyncio
async def test_rerun_budget_adds_up_and_only_three_retries_after_platform_errors(p):
    request, expert, test = await p.self_expert()
    found = await p.deliveries.submit(
        OWNER,
        str(expert.id),
        DeliverySubmitIn(
            listing="public", authorize_reviewer=True, budget_limit=Decimal("5")
        ),
        user_token,
    )
    first = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert p.runtime.started[-1][1].budget.limit == "5.00"
    await p.repo.update_test(
        first.id,
        status="platform_error",
        report={**(first.report or {}), "cost": {"spent": "3", "currency": "CNY"}},
    )
    second = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert p.runtime.started[-1][1].budget.limit == "2.00"  # 5 减去第一次花的 3
    for _ in range(2):
        await p.repo.update_test(
            second.id,
            status="platform_error",
            report={"cost": {"spent": "0.5", "currency": "CNY"}},
        )
        second = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert (await p.repo.get_delivery(found.id)).rerun_attempts == 4
    await p.repo.update_test(second.id, status="platform_error")
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert caught.value.code == "rerun_attempts_used"


@pytest.mark.asyncio
async def test_a_rerun_with_its_budget_used_up_is_not_started(p):
    _, expert, _ = await p.self_expert()
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    first = await p.deliveries.start_rerun(str(found.id), "ops-1")
    await p.repo.update_test(
        first.id, status="platform_error", report={"cost": {"spent": "5"}}
    )
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert caught.value.code == "rerun_budget_used"


@pytest.mark.asyncio
async def test_grants_signed_before_a_rerun_grant_fails_are_revoked(p):
    """两条用例：第一张签了、第二张被拒——第一张跟着撤（原来漏撤）。"""
    request, expert, test = await p.self_expert()
    key = (request.id, 1)
    v = p.repo.versions[key]
    p.repo.versions[key] = replace(v, cases=[*v.cases, {**v.cases[0], "case_id": "c2"}])
    p.repo.test_sets.append(_public_set(request, test, cases=("c1", "c2")))
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    p.knowbase.fail_grant_after = 1
    with pytest.raises(AppException):
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert p.knowbase.revoked == ["grant-1"]


@pytest.mark.asyncio
async def test_a_rerun_started_while_the_author_withdraws_is_stopped(p):
    """发起重跑与作者撤回撞上（插桩在开跑那一刻撤回）：写重跑编号前锁里核，不在审了
    就立即停掉这次重跑。"""
    _, expert, _ = await p.self_expert()
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)

    async def withdraw_meanwhile():
        p.runtime.on_start = None
        await p.deliveries._withdraw(found, by=OWNER, reason="作者撤回")

    p.runtime.on_start = withdraw_meanwhile
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert caught.value.code == "delivery_state"
    _, run = p.runtime.started[-1]
    assert p.runtime.cancelled == [run.run_id]
    assert (await p.repo.get_delivery(found.id)).rerun_test_id is None


@pytest.mark.asyncio
async def test_a_failed_cleanup_after_a_failed_submit_is_left_for_the_sweep(p):
    """提交没成、补撤也没撤成：留一行撤回了的交付，后台补撤扫得到。"""
    _, expert, _ = await p.self_expert()
    p.knowbase.fail_consent_kind = "rerun"
    real_end = p.knowbase.end_platform

    async def down(**kw):
        raise ServiceUnavailableError("down", code="knowbase_unavailable")

    p.knowbase.end_platform = down
    with pytest.raises(AppException):
        await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    [left] = p.repo.deliveries.values()
    assert (left.status, left.consent_ended_at) == ("withdrawn", None)
    assert stage_of(await p.repo.get_expert(expert.id)) == "awaiting_user"
    p.knowbase.end_platform = real_end
    assert await p.deliveries.close_pending() == 1
    assert {e["kind"] for e in p.knowbase.ended} == {"review", "rerun"}
    assert (await p.repo.get_delivery(left.id)).consent_ended_at is not None


@pytest.mark.asyncio
async def test_the_directory_backs_off_and_stops_when_knowbase_is_down(p):
    first, _, _ = await p.self_expert()
    second, _, _ = await p.self_expert()
    p.knowbase.fail = True
    assert await p.deliveries.sync_directory() == 0
    sent = [p.repo.requests[r.id].directory_sent for r in (first, second)]
    # 不可达：试了第一个就停，不对每个名称都超时一遍；试过的记次数、往后排
    assert sent[0]["failures"] == 1 and sent[1] is None
    p.knowbase.fail = False
    assert await p.deliveries.sync_directory() == 1  # 第一个还没到再试的时候
    p.deliveries.now = lambda: NOW + timedelta(minutes=5)
    assert await p.deliveries.sync_directory() == 1
    assert await p.deliveries.sync_directory() == 0


@pytest.mark.parametrize(
    "text",
    ["上调公司主体信用评级至 AA+", "评级机构下调债项评级", "信用评级为 AAA"],
)
def test_credit_ratings_are_facts_not_investment_ratings(text):
    found = auto_check(
        report={"cases": [{"case_id": "c1", "status": "passed"}]},
        test_status="passed",
        accepted=True,
        case_ids=["c1"],
        outputs={"c1": {"deliverable": {"text": text}}},
    )
    assert found == []


@pytest.mark.asyncio
async def test_counts_include_an_undelivered_conversion_and_replies_hide_bindings(p):
    """「等我们」把审过却没交成的（含转公开）也算上；审核、作废、重试的回包从运营看的
    那一份里取（审完的不带绑定）。"""
    from app.application.services.knowbot_board import admin_counts
    from app.interfaces.endpoints.knowbot_routes import _operator_delivery

    _, expert, _ = await p.platform_expert()
    await p.experts.return_to_user(str(expert.id), "ops-1")
    p.knowbase.bound = [dict(BOUND)]
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    turned = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    p.knowmart.fail = True
    done = await p.deliveries.review(
        str(turned.id),
        DeliveryReviewIn(approved=True, category="tools.lookup"),
        "ops-1",
    )
    assert done.delivered_at is None
    board = KnowbotBoard(p.repo, now=lambda: NOW)
    assert admin_counts(await board.lives(operator=True))["delivery"] == 1
    reply = await _operator_delivery(board, done)
    assert reply["bindings"] == [] and reply["binding_check"] is None


@pytest.mark.asyncio
async def test_freeze_is_voided_on_submit_failure_and_sweep_skips_reopened_expert(p):
    """第 1 条：提交在 freeze 后失败会退回冻结；若退回冻结也失败则留给 close_pending
    补作废，但补作废在锁里看到该版已有在走的交付时绝不作废新交付的冻结。"""
    _, expert, _ = await p.self_expert()
    p.knowbase.fail_consent_kind = "review"
    with pytest.raises(AppException):
        await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    target = f"expert:{expert.request_id}@{expert.version}"
    assert p.knowbase.voided == [target]

    # 撤回时 void_frozen 失败 -> 留在 close_pending；若作者随后重交，补扫不作废新冻结
    p.knowbase.fail_consent_kind = None
    found = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    real_void = p.knowbase.void_frozen

    async def void_down(*a, **kw):
        raise ServiceUnavailableError("down", code="knowbase_unavailable")

    p.knowbase.void_frozen = void_down
    await p.deliveries.withdraw(OWNER, str(expert.id))
    assert (await p.repo.get_delivery(found.id)).consent_ended_at is None
    p.knowbase.void_frozen = real_void
    # 作者还没重交时，补扫会作废冻结
    assert await p.deliveries.close_pending() == 1
    assert p.knowbase.voided == [target, target]

    # 再测：撤回时 void_frozen 失败，作者立刻重交 -> 补扫锁里看到已有在走交付，不作废
    again = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    p.knowbase.void_frozen = void_down
    await p.deliveries.withdraw(OWNER, str(expert.id))
    p.knowbase.void_frozen = real_void
    reopened = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    assert reopened.status == "reviewing"
    assert await p.deliveries.close_pending() == 1
    assert p.knowbase.voided == [target, target]  # 没有多作废一次
    assert (await p.repo.get_delivery(again.id)).consent_ended_at is not None


@pytest.mark.asyncio
async def test_stopped_version_in_knowmart_does_not_block_withdraw_and_stop_is_swept(
    p,
):
    """第 2 条：knowmart 里 status=='stopped' 的版本不算已收下；交 knowmart 撞上撤回且停
    knowmart 失败时记入 pending_knowmart，由 close_pending 补停。"""
    _, expert, _ = await p.self_expert()
    p.knowmart.fail = True
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    p.knowmart.fail = False
    p.knowmart.version_states = [
        {
            "version": "1",
            "status": "stopped",
            "held_reason": None,
            "public_allowed": False,
        }
    ]
    withdrawn = await p.deliveries.withdraw(OWNER, str(expert.id))
    assert withdrawn.status == "withdrawn"

    # 第二位专家：交 knowmart 撞上撤回，且当时 knowmart.stop 失败 -> close_pending 补停
    _, expert2, _ = await p.self_expert()
    p.knowmart.version_states = []
    p.knowmart.fail = True
    found2 = await p.deliveries.submit(OWNER, str(expert2.id), SELF, user_token)
    p.knowmart.fail = False
    p.knowmart.fail_stop = True

    async def withdraw_meanwhile():
        p.knowmart.on_deliver = None
        await p.deliveries._withdraw(found2, by=OWNER, reason="作者撤回")

    p.knowmart.on_deliver = withdraw_meanwhile
    with pytest.raises(ConcurrencyConflictError):
        await p.deliveries.retry(str(found2.id))
    left = await p.repo.get_delivery(found2.id)
    assert left.consent_ended_at is None
    assert left.consents.get("pending_knowmart", {}).get("action") == "stop"
    assert (await p.repo.get_expert(expert2.id)).stopped_at is not None
    p.knowmart.fail_stop = False
    assert await p.deliveries.close_pending() == 1
    assert p.knowmart.stopped[-1]["knowbot_id"] == "kb-1"
    assert (await p.repo.get_delivery(found2.id)).consent_ended_at is not None


@pytest.mark.asyncio
async def test_withdraw_and_reject_stop_rerun_using_post_lock_delivery_record(p):
    """第 3 条：撤回与审核不通过都用锁里重读出的 current/found 去停重跑，哪怕锁前旧快照
    里 rerun_test_id 还是 None，并发刚写进去的重跑也会被停掉。"""
    _, expert, _ = await p.self_expert()
    stale = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    assert stale.rerun_test_id is None
    rerun = await p.deliveries.start_rerun(str(stale.id), "ops-1")
    # 传入 rerun_test_id=None 的旧快照 stale，锁里读到最新 rerun_test_id 并停掉
    await p.deliveries._withdraw(stale, by=OWNER, reason="作者撤回")
    assert p.runtime.cancelled == [rerun.run_id]

    _, expert2, _ = await p.self_expert()
    sub2 = await p.deliveries.submit(OWNER, str(expert2.id), PUBLIC, user_token)
    rerun2 = await p.deliveries.start_rerun(str(sub2.id), "ops-1")
    await p.deliveries.review(
        str(sub2.id), DeliveryReviewIn(approved=False, reason="不合要求"), "ops-1"
    )
    assert rerun2.run_id in p.runtime.cancelled


@pytest.mark.asyncio
async def test_stopping_an_expert_closes_its_in_flight_conversion_delivery(p):
    """第 4 条：已自用上架的专家在转公开审核中（含重跑）被停用时，一并停重跑、结授权、
    把在途交付改为 withdrawn。"""
    _, expert, _ = await p.self_expert()
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    conv = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    rerun = await p.deliveries.start_rerun(str(conv.id), "ops-1")
    await p.experts.stop(str(expert.id), "ops-1", "违规停用")
    closed = await p.repo.get_delivery(conv.id)
    assert closed.status == "withdrawn" and closed.consent_ended_at is not None
    assert rerun.run_id in p.runtime.cancelled
    assert any(e["ref"] == str(conv.id) for e in p.knowbase.ended)


@pytest.mark.asyncio
async def test_rerun_with_unknown_cost_counts_its_full_run_budget(p):
    """第 5 条：重跑因超时、停跑或没拿到花费（cost 为空）结束时，按当次给它的预算上限
    全额计入已花。"""
    _, expert, _ = await p.self_expert()
    found = await p.deliveries.submit(
        OWNER,
        str(expert.id),
        DeliverySubmitIn(
            listing="public", authorize_reviewer=True, budget_limit=Decimal("5")
        ),
        user_token,
    )
    first = await p.deliveries.start_rerun(str(found.id), "ops-1")
    await p.repo.update_test(
        first.id,
        status="platform_error",
        error="timed out",
        budget_limit="3.00",
        report={"cost": {"spent": "0.1", "currency": "CNY"}},
    )
    # 第一次超时：忽略进度里的 0.1，按当次预算 3.00 全额计入，第二次只剩 2.00
    second = await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert (await p.repo.get_delivery(found.id)).rerun_spent == Decimal("3.00")
    assert p.runtime.started[-1][1].budget.limit == "2.00"
    # 第二次也没拿到 cost：再把当次预算 2.00 计满，不可再发起第三次
    await p.repo.update_test(second.id, status="platform_error", report={"cost": None})
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.start_rerun(str(found.id), "ops-1")
    assert caught.value.code == "rerun_budget_used"


@pytest.mark.asyncio
async def test_raise_frozen_still_rejects_current_self_licence_before_raising_any(p):
    """第 7 条：既有冻结为 self 但当前已改为 via_expert 的材料，又有当前仍是 self 的
    材料时，哪怕传了 raise_frozen=True 也直接报 binding_not_shareable，且不升档。"""
    _, expert, _ = await p.platform_expert()
    await p.experts.return_to_user(str(expert.id), "ops-1")
    p.knowbase.bound = [
        dict(BOUND, material_id="m-1", license="self"),
        dict(BOUND, material_id="m-2", license="self", sha256="c" * 64),
    ]
    p.knowbase.states["m-1"] = {"state": "live", "license": "self"}
    p.knowbase.states["m-2"] = {"state": "live", "license": "self"}
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    # 现在 m-1 改成了 via_expert，但 m-2 当前仍是 self
    p.knowbase.states["m-1"] = {"state": "live", "license": "via_expert"}
    p.knowbase.states["m-2"] = {"state": "live", "license": "self"}
    raised = DeliverySubmitIn(
        listing="public", authorize_reviewer=True, raise_frozen=True
    )
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.submit(OWNER, str(expert.id), raised, user_token)
    assert caught.value.code == "binding_not_shareable"
    assert p.knowbase.raised == []


@pytest.mark.asyncio
async def test_withdrawing_a_conversion_while_knowmart_takes_it_keeps_self_listing(p):
    """第 8 条：自用转公开交 knowmart 撞上作者撤回时，调 knowmart.withdraw_public 只撤
    公开、不停整版，专家在 knowlab 仍保持自用已上架。"""
    _, expert, _ = await p.platform_expert()
    await p.experts.return_to_user(str(expert.id), "ops-1")
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    p.knowmart.fail = True
    conv = await p.deliveries.submit(OWNER, str(expert.id), PUBLIC, user_token)
    await p.deliveries.review(
        str(conv.id),
        DeliveryReviewIn(approved=True, category="tools.lookup"),
        "ops-1",
    )
    p.knowmart.fail = False

    async def withdraw_meanwhile():
        p.knowmart.on_deliver = None
        await p.deliveries._withdraw(conv, by=OWNER, reason="作者撤回转公开")

    p.knowmart.on_deliver = withdraw_meanwhile
    with pytest.raises(ConcurrencyConflictError) as caught:
        await p.deliveries.retry(str(conv.id))
    assert caught.value.code == "delivery_state"
    assert p.knowmart.public_withdrawn == [
        {
            "owner_subject": OWNER,
            "knowbot_id": "kb-1",
            "version": "1",
        }
    ]
    assert p.knowmart.stopped == []
    after = await p.repo.get_expert(expert.id)
    assert after.stopped_at is None and after.listed_at is not None
    assert after.review_listing == "self"


@pytest.mark.asyncio
async def test_sync_market_merges_against_locked_expert_delivery(p):
    """第 9 条：sync_market 写回前在事务里锁行重读 expert.delivery，不拿远端等待前的旧
    快照覆盖并发刚写进去的 delivery 字段。"""
    _, expert, _ = await p.self_expert()
    await p.deliveries.submit(OWNER, str(expert.id), SELF, user_token)
    real_versions = p.knowmart.versions

    async def versions_with_concurrent_write(**kw):
        cur = await p.repo.get_expert(expert.id)
        await p.repo.update_expert(
            expert.id, delivery={**(cur.delivery or {}), "concurrent_marker": "kept"}
        )
        return await real_versions(**kw)

    p.knowmart.versions = versions_with_concurrent_write
    p.knowmart.version_states = [
        {
            "version": "1",
            "status": "held",
            "held_reason": "binding_withdrawn",
            "public_allowed": False,
        }
    ]
    await p.experts.sync_market()
    updated = await p.repo.get_expert(expert.id)
    assert updated.delivery.get("concurrent_marker") == "kept"
    assert updated.delivery.get("version_status") == "held"
