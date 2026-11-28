"""Verify assumptions used by lifecycle tests independently of those tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.ports.knowbots import TestSetRecord as FrozenSet
from app.domain.knowbots.compile import pack_problems
from tests.knowbot_fakes import FakeKnowbaseDelivery, MemoryKnowlab, manifest, result


def test_shared_expert_can_pass_pack_validation():
    assert pack_problems(manifest()["pack"]) == []


def test_passed_receipt_names_a_step_in_the_shared_expert():
    units = {unit["contract"]["step_id"] for unit in manifest()["pack"]["method_units"]}
    receipt = result("local-test-run")
    assert all(step.step_id in units for case in receipt.cases for step in case.steps)


@pytest.mark.asyncio
async def test_test_set_rolls_back_with_its_transaction():
    repo = MemoryKnowlab()
    row = FrozenSet(uuid4(), uuid4(), 1, {}, datetime.now(UTC))
    with pytest.raises(RuntimeError):
        async with repo.transaction():
            await repo.add_test_set(row)
            raise RuntimeError("synthetic failure before commit")
    assert await repo.latest_test_set(row.request_id) is None


@pytest.mark.asyncio
async def test_active_consent_matches_the_creation_and_closure_receipts():
    delivery = FakeKnowbaseDelivery()
    args = dict(kind="review", ref="local-review")
    created = await delivery.consent_platform("local-token", **args, items=[])
    assert (
        await delivery.platform_consent_live(subject="local-owner", **args) == created
    )


@pytest.mark.asyncio
async def test_ended_consent_is_no_longer_live():
    delivery = FakeKnowbaseDelivery()
    args = dict(kind="review", ref="local-review")
    await delivery.consent_platform(
        "local-token", **args, items=[{"material_id": "local"}]
    )
    await delivery.end_platform(subject="local-owner", **args, reason="finished")
    assert await delivery.platform_consent_live(subject="local-owner", **args) == 0


@pytest.mark.asyncio
async def test_identical_questions_in_one_report_are_stored_once():
    repo = MemoryKnowlab()
    question = dict(
        question_id="q1",
        case_id="c1",
        kind="question",
        text="使用哪个期间？",
        asked_at=datetime.now(UTC),
    )
    await repo.add_questions(uuid4(), "local-run", [question, question])
    assert len(await repo.list_questions()) == 1
    incubation = SimpleNamespace(id=uuid4(), request_id=uuid4(), run_id="local-run")
    await repo.add_gaps(incubation, [question, question])
    assert len(await repo.list_gaps(incubation_id=incubation.id)) == 1
