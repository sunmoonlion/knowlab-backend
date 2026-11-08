"""Real PostgreSQL: exceptions preserve verdicts and keep delivery authorization."""

import asyncio
import uuid
from dataclasses import replace

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.errors.exceptions import (
    ConcurrencyConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.application.services.knowbot_manual_release import KnowbotManualReleaseService
from app.domain.knowbots.delivery import DeliveryReviewIn, DeliverySubmitIn
from app.domain.knowbots.manual_release import (
    ManualReleaseDecision,
    ManualReleaseIn,
    manifest_digest,
)
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.knowbot_fakes import OWNER, manifest, platform_draft, result, user_token
from tests.test_knowbot_repository_db import (
    RUNTIME,
    _reviewed,
    _start,
    deliveries,
    service,
)
from tests.test_knowbot_repository_db import (
    db as db,
)


def manual(session):
    return KnowbotManualReleaseService(SqlKnowlabRepository(session))


async def candidate(db):
    request, old = await _reviewed(db)
    baseline = await _start(db, old)
    RUNTIME.results[baseline.run_id] = result(baseline.run_id)
    async with db() as session:
        await service(session).refresh(str(baseline.id))
    async with db() as session:
        draft = await platform_draft(service(session), str(request.id), manifest("2"))
    async with db() as session:
        await service(session).review_rules(str(draft.id), "reviewer")
    payload = ManualReleaseIn(
        baseline_test_id=baseline.id,
        manifest_sha256=manifest_digest(draft.manifest),
        reason="Owner requests release without another paid model run",
        change_summary="Correct opening to previous closing stock balances",
        verification="Offline positive and negative regression; not a model run",
        limitations="No acceptance run of the revised expert",
        authorize_reviewer=True,
        acknowledge_unretested=True,
    )
    return request, old, baseline, draft, payload


def approve():
    return ManualReleaseDecision(
        approved=True,
        reason="Reviewed exact correction and offline evidence",
        acknowledge_unretested=True,
    )


async def request_exception(db, draft, payload):
    async with db() as session:
        return await manual(session).request(OWNER, str(draft.id), payload)


async def test_manual_release_does_not_fabricate_test_or_skip_delivery(db):
    request, old, baseline, draft, payload = await candidate(db)
    requested = await request_exception(db, draft, payload)
    assert (await request_exception(db, draft, payload)).id == requested.id
    async with db() as session:
        approved = await manual(session).decide(
            str(requested.id), "release-person", approve()
        )
        assert approved.status == "approved"
    async with db() as session:
        again = await manual(session).decide(
            str(requested.id), "release-person", approve()
        )
        assert again.expert_id == approved.expert_id
        repo = SqlKnowlabRepository(session)
        tests = await repo.list_tests(request_ids=[request.id])
        assert [t.id for t in tests] == [baseline.id]
        assert (await repo.get_draft(old.id)).status == "passed"
        assert (await repo.get_draft(draft.id)).status == "pending_test"
        expert = await repo.get_expert(approved.expert_id)
        assert expert.test_id is None and expert.manual_release["retested"] is False
        assert expert.manual_release["approved_by"] == "release-person"
    async with db() as session:
        with pytest.raises(ConcurrencyConflictError):
            await service(session).start_test(
                str(draft.id), budget_limit="20", timeout_seconds=600, starter="admin-1"
            )
    async with db() as session:
        delivery = await deliveries(session).submit(
            OWNER,
            str(approved.expert_id),
            DeliverySubmitIn(listing="public", authorize_reviewer=True),
            user_token,
        )
        assert delivery.status == "reviewing"
        assert delivery.test_mode == "unrun" and not delivery.needs_rerun
        assert delivery.auto_check["passed"] is False
        assert delivery.auto_check["findings"] == [{"kind": "manual_unretested"}]
    async with db() as session:
        reviewed = await deliveries(session).review(
            str(delivery.id),
            DeliveryReviewIn(approved=True, category="tools.calculator"),
            "release-person",
        )
        assert reviewed.status == "approved" and reviewed.delivered_at is not None
        assert (
            len(
                await SqlKnowlabRepository(session).list_tests(request_ids=[request.id])
            )
            == 1
        )


async def test_author_scope_digest_and_baseline_are_checked(db):
    _, _, _, draft, payload = await candidate(db)
    async with db() as session:
        with pytest.raises(NotFoundError):
            await manual(session).request("someone-else", str(draft.id), payload)
    for changes in [{"manifest_sha256": "0" * 64}, {"baseline_test_id": uuid.uuid4()}]:
        async with db() as session:
            with pytest.raises(ConcurrencyConflictError):
                await manual(session).request(
                    OWNER, str(draft.id), payload.model_copy(update=changes)
                )
    await request_exception(db, draft, payload)
    with pytest.raises(ConcurrencyConflictError):
        await request_exception(
            db, draft, payload.model_copy(update={"reason": "different"})
        )
    with pytest.raises(ValidationError):
        ManualReleaseIn.model_validate(
            payload.model_dump() | {"acknowledge_unretested": False}
        )


async def test_active_or_finished_test_cannot_be_overridden(db):
    _, _, _, draft, payload = await candidate(db)
    run = await _start(db, draft)
    with pytest.raises(ConcurrencyConflictError):
        await request_exception(db, draft, payload)
    RUNTIME.results[run.run_id] = result(run.run_id, "failed")
    async with db() as session:
        await service(session).refresh(str(run.id))
    with pytest.raises(ConcurrencyConflictError):
        await request_exception(db, draft, payload)


async def test_two_reviewers_cannot_create_two_experts(db):
    request, _, _, draft, payload = await candidate(db)
    requested = await request_exception(db, draft, payload)

    async def decision(actor):
        async with db() as session:
            return await manual(session).decide(str(requested.id), actor, approve())

    responses = await asyncio.gather(
        decision("one"), decision("two"), return_exceptions=True
    )
    assert sum(isinstance(x, ConcurrencyConflictError) for x in responses) == 1
    async with db() as session:
        assert len(await SqlKnowlabRepository(session).list_experts([request.id])) == 2


async def test_audit_history_is_immutable_and_rejection_is_terminal(db):
    _, _, _, draft, payload = await candidate(db)
    requested = await request_exception(db, draft, payload)
    for sql in [
        "DELETE FROM knowbot_manual_release WHERE id=:id",
        "UPDATE knowbot_manual_release SET reason='changed' WHERE id=:id",
    ]:
        async with db() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text(sql), {"id": requested.id})
                await session.commit()
    async with db() as session:
        rejected = await manual(session).decide(
            str(requested.id),
            "reviewer",
            ManualReleaseDecision(
                approved=False,
                reason="Evidence insufficient",
                acknowledge_unretested=True,
            ),
        )
        assert rejected.expert_id is None
    async with db() as session:
        with pytest.raises(ConcurrencyConflictError):
            await manual(session).decide(str(requested.id), "reviewer", approve())
    async with db() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE knowbot_manual_release SET status='pending', "
                    "decided_at=NULL WHERE id=:id"
                ),
                {"id": requested.id},
            )


async def test_delivery_rechecks_exact_approved_content(db):
    _, _, _, draft, payload = await candidate(db)
    requested = await request_exception(db, draft, payload)
    async with db() as session:
        approved = await manual(session).decide(
            str(requested.id), "reviewer", approve()
        )
        expert = await SqlKnowlabRepository(session).get_expert(approved.expert_id)
        for changed in [
            replace(expert, test_id=payload.baseline_test_id),
            replace(
                expert,
                manual_release=expert.manual_release | {"manifest_sha256": "0" * 64},
            ),
        ]:
            with pytest.raises(ConcurrencyConflictError):
                await deliveries(session)._manual_release_valid(changed)


async def test_the_author_cannot_decide_their_own_release(db):
    """特批放行的人不能是申请的作者（所有者 2026-11-08 定，不留例外）：批、拒都不行，
    申请还在待批；换一位放行人照常批。"""
    request, old, baseline, draft, payload = await candidate(db)
    requested = await request_exception(db, draft, payload)
    reject = ManualReleaseDecision(
        approved=False, reason="自己拒自己", acknowledge_unretested=True
    )
    for decision in (approve(), reject):
        async with db() as session:
            with pytest.raises(ForbiddenError) as caught:
                await manual(session).decide(str(requested.id), OWNER, decision)
            assert caught.value.code == "self_review_forbidden"
    async with db() as session:
        still = await SqlKnowlabRepository(session).get_manual_release(requested.id)
        assert still.status == "pending"
        approved = await manual(session).decide(
            str(requested.id), "release-person", approve()
        )
        assert approved.status == "approved"
