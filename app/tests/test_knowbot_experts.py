"""专家：交回、两道门（先用户、后运营）、上架、停用（SDD 0037 第五、第六节）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.errors.exceptions import (
    AppException,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_experts import KnowbotExpertService, stage_of
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.lifecycle import ExpertDecisionIn, ExpertReviewIn
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
    manifest,
    request_body,
    result,
)

NOW = datetime(2026, 10, 24, 9, tzinfo=UTC)


@pytest.fixture
def parts():
    repo, runtime, knowmart = MemoryKnowlab(), FakeRuntime(), FakeKnowmart()
    creation = KnowbotCreationService(repo, runtime, now=lambda: NOW)
    experts = KnowbotExpertService(repo, knowmart, now=lambda: NOW)
    return creation, experts, repo, runtime, knowmart


async def passed_expert(creation: KnowbotCreationService, runtime: FakeRuntime):
    request = await creation.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await creation.accept(str(request.id), 1, all_clear(), "admin-1")
    draft = await creation.add_draft(str(request.id), manifest())
    await creation.review_rules(str(draft.id), "admin-1")
    test = await creation.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600
    )
    runtime.results[test.run_id] = result(test.run_id, "passed")
    await creation.refresh(str(test.id))
    [expert] = creation.repo.experts.values()  # type: ignore[attr-defined]
    return request, expert


SELF = ExpertReviewIn(approved=True, listing="self")


@pytest.mark.asyncio
async def test_two_gates_then_knowmart_gets_exactly_the_tested_manifest(parts):
    creation, experts, repo, runtime, knowmart = parts
    request, expert = await passed_expert(creation, runtime)
    eid = str(expert.id)
    assert stage_of(expert) == "to_return"
    with pytest.raises(NotFoundError):  # 没交回的用户看不到
        await experts.my_expert(OWNER, eid)
    with pytest.raises(ConcurrencyConflictError):  # 用户没同意不能审
        await experts.review(eid, SELF, "ops-1")

    returned = await experts.return_to_user(eid, "ops-1")
    assert stage_of(returned) == "awaiting_user"
    with pytest.raises(ConcurrencyConflictError):
        await experts.return_to_user(eid, "ops-1")
    with pytest.raises(NotFoundError):
        await experts.decide("someone-else", eid, ExpertDecisionIn(accept=True))
    with pytest.raises(ConcurrencyConflictError):
        await experts.review(eid, SELF, "ops-1")

    accepted = await experts.decide(OWNER, eid, ExpertDecisionIn(accept=True))
    assert stage_of(accepted) == "to_review"
    assert (accepted.user_decided_by, accepted.user_decided_at) == (OWNER, NOW)
    with pytest.raises(ConcurrencyConflictError):
        await experts.decide(OWNER, eid, ExpertDecisionIn(accept=False))

    listed = await experts.review(eid, SELF, "ops-1")
    assert stage_of(listed) == "listed" and listed.listed_at == NOW
    assert (listed.knowmart_knowbot_id, listed.digest) == ("kb-1", "sha256:" + "d" * 64)
    assert knowmart.delivered == [
        {
            "delivery_id": f"knowlab-expert-{expert.id}",
            "owner_subject": OWNER,
            "knowbot_ref": f"knowlab-{request.id}",
            "manifest": manifest(),
            "request_public": False,
        }
    ]


@pytest.mark.asyncio
async def test_the_user_may_decline_and_nothing_is_listed(parts):
    creation, experts, _, runtime, knowmart = parts
    _, expert = await passed_expert(creation, runtime)
    await experts.return_to_user(str(expert.id), "ops-1")
    declined = await experts.decide(
        OWNER, str(expert.id), ExpertDecisionIn(accept=False)
    )
    assert stage_of(declined) == "declined"
    with pytest.raises(ConcurrencyConflictError):
        await experts.review(str(expert.id), SELF, "ops-1")
    assert knowmart.delivered == []


@pytest.mark.asyncio
async def test_a_rejection_needs_a_reason_and_is_not_delivered(parts):
    creation, experts, _, runtime, knowmart = parts
    _, expert = await passed_expert(creation, runtime)
    await experts.return_to_user(str(expert.id), "ops-1")
    await experts.decide(OWNER, str(expert.id), ExpertDecisionIn(accept=True))
    with pytest.raises(ValueError):
        ExpertReviewIn(approved=False)
    with pytest.raises(ValueError):
        ExpertReviewIn(approved=True)  # 通过要定自用还是公开
    rejected = await experts.review(
        str(expert.id),
        ExpertReviewIn(approved=False, reason="成稿里有目标价（C-L5）"),
        "ops-1",
    )
    assert stage_of(rejected) == "rejected"
    assert rejected.review_reason == "成稿里有目标价（C-L5）"
    assert knowmart.delivered == []
    with pytest.raises(ConcurrencyConflictError):
        await experts.deliver(str(expert.id))


@pytest.mark.asyncio
async def test_a_failed_delivery_keeps_the_review_and_can_be_retried(parts):
    creation, experts, _, runtime, knowmart = parts
    _, expert = await passed_expert(creation, runtime)
    await experts.return_to_user(str(expert.id), "ops-1")
    await experts.decide(OWNER, str(expert.id), ExpertDecisionIn(accept=True))
    knowmart.fail = True
    reviewed = await experts.review(
        str(expert.id), ExpertReviewIn(approved=True, listing="public"), "ops-1"
    )
    assert stage_of(reviewed) == "to_deliver"
    assert reviewed.delivery_error == "delivery failed: knowmart_unavailable"
    with pytest.raises(AppException):
        await experts.deliver(str(expert.id))
    knowmart.fail = False
    listed = await experts.deliver(str(expert.id))
    assert stage_of(listed) == "listed" and listed.delivery_error is None
    # 定了公开：交付带申请公开，knowmart 回上架状态
    assert knowmart.delivered[-1]["request_public"] is True
    assert listed.delivery and listed.delivery["listing_status"] == "pending"
    with pytest.raises(ConcurrencyConflictError):
        await experts.deliver(str(expert.id))  # 交过了不重复交


@pytest.mark.asyncio
async def test_stopping_stops_knowmart_first_by_the_user_or_by_us(parts):
    creation, experts, _, runtime, knowmart = parts
    _, expert = await passed_expert(creation, runtime)
    eid = str(expert.id)
    with pytest.raises(ConcurrencyConflictError):  # 没上架的停不了
        await experts.stop(eid, "不要了", "ops-1")
    await experts.return_to_user(eid, "ops-1")
    await experts.decide(OWNER, eid, ExpertDecisionIn(accept=True))
    await experts.review(eid, SELF, "ops-1")
    with pytest.raises(NotFoundError):
        await experts.stop(eid, "不要了", "x", owner_subject="someone-else")
    knowmart.fail = True
    with pytest.raises(AppException):  # knowmart 没停成，这边不记
        await experts.stop(eid, "不要了", OWNER, owner_subject=OWNER)
    assert stage_of((await experts.expert(eid))[0]) == "listed"
    knowmart.fail = False
    stopped = await experts.stop(eid, "不要了", OWNER, owner_subject=OWNER)
    assert stage_of(stopped) == "stopped"
    assert (stopped.stopped_by, stopped.stop_reason) == (OWNER, "不要了")
    assert knowmart.stopped == [
        {"owner_subject": OWNER, "knowbot_id": "kb-1", "version": "1"}
    ]
    with pytest.raises(ConcurrencyConflictError):
        await experts.stop(eid, "再停", "ops-1")


@pytest.mark.asyncio
async def test_a_trial_expert_gets_a_review_record_without_delivery(parts):
    creation, experts, repo, runtime, knowmart = parts
    _, expert = await passed_expert(creation, runtime)
    # 试行期上架的（迁移回填）：两道门之前交付
    await repo.update_expert(
        expert.id, listed_at=NOW, knowmart_knowbot_id="kb-1", returned_at=NOW
    )
    repo.experts[expert.id] = replace(repo.experts[expert.id], trial=True)
    reviewed = await experts.review(str(expert.id), SELF, "ops-1")
    assert stage_of(reviewed) == "listed" and reviewed.reviewed_by == "ops-1"
    assert knowmart.delivered == []
    with pytest.raises(ConcurrencyConflictError):  # 补一次就够
        await experts.review(str(expert.id), SELF, "ops-1")


@pytest.mark.asyncio
async def test_knowmart_learns_what_became_of_versions_never_listed(parts):
    """SDD 0037 第三节：knowmart 详情页写明空号那一版——不要、审核不过时逐条交结局；
    交不到的记着，定时补发，送到为止（Astra 评审 F08）。"""
    creation, experts, repo, runtime, knowmart = parts
    request, expert = await passed_expert(creation, runtime)
    await experts.return_to_user(str(expert.id), "ops-1")
    knowmart.fail = True  # 交不到不挡用户的决定
    declined = await experts.decide(
        OWNER, str(expert.id), ExpertDecisionIn(accept=False)
    )
    assert stage_of(declined) == "declined" and knowmart.unlisted == []
    assert declined.unlisted_sent_at is None  # 还要送
    assert await experts.resend_unlisted() == 0  # 还是送不到
    knowmart.fail = False
    assert await experts.resend_unlisted() == 1
    assert knowmart.unlisted == [
        {
            "owner_subject": OWNER,
            "knowbot_ref": f"knowlab-{request.id}",
            "version": "1",
            "outcome": "declined",
        }
    ]
    sent = await repo.get_expert(expert.id)
    assert sent is not None and sent.unlisted_sent_at is not None
    assert await experts.resend_unlisted() == 0  # 送到的不再送
