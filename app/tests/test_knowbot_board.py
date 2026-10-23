"""两端的栏、大类、数、详情页的记录（SDD 0037 第七至第九节）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.services.knowbot_board import (
    KnowbotBoard,
    acceptance_items,
    admin_counts,
    application_category,
    delivery_items,
    inbox_items,
    incubation_items,
    lab_test_items,
    summary,
    timeline,
    user_expert_items,
)
from app.application.services.knowbot_experts import KnowbotExpertService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.lifecycle import ExpertDecisionIn, UserReviewIn
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
    repo, runtime = MemoryKnowlab(), FakeRuntime()
    creation = KnowbotCreationService(repo, runtime, now=lambda: NOW)
    experts = KnowbotExpertService(repo, FakeKnowmart(), now=lambda: NOW)
    return creation, experts, KnowbotBoard(repo), runtime


async def _submit(creation: KnowbotCreationService, owner: str = OWNER):
    return await creation.submit(owner, KnowbotRequestIn.model_validate(request_body()))


def _categories(items) -> list[str]:  # type: ignore[no-untyped-def]
    return [item[0] for item in items]


@pytest.mark.asyncio
async def test_the_admin_columns_follow_the_life(parts):
    creation, experts, board, runtime = parts
    request = await _submit(creation)
    lives = await board.lives()
    assert _categories(acceptance_items(lives)) == ["pending"]
    assert admin_counts(lives) == {
        "acceptance": 1,
        "incubation": 0,
        "tests": 0,
        "delivery": 0,
    }

    await creation.accept(str(request.id), 1, all_clear(), "admin-1")
    lives = await board.lives()
    assert _categories(acceptance_items(lives)) == ["accepted"]
    assert _categories(incubation_items(lives)) == ["todo"]
    assert admin_counts(lives)["incubation"] == 1

    draft = await creation.add_draft(str(request.id), manifest())
    await creation.request_user_review(str(draft.id), "admin-1")
    lives = await board.lives()
    assert _categories(incubation_items(lives)) == ["in_progress"]
    assert lab_test_items(lives) == []  # 等用户审核的还不能测

    await creation.answer_user_review(OWNER, str(draft.id), UserReviewIn(ok=True))
    lives = await board.lives()
    assert _categories(incubation_items(lives)) == ["done"]
    [(category, _, item)] = lab_test_items(lives)
    assert category == "to_start" and item.id == draft.id
    assert admin_counts(lives)["tests"] == 1

    await creation.review_rules(str(draft.id), "admin-1")
    test = await creation.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600
    )
    lives = await board.lives()
    assert _categories(lab_test_items(lives)) == ["queued"]
    assert admin_counts(lives)["tests"] == 0

    runtime.results[test.run_id] = result(test.run_id, "passed")
    await creation.refresh(str(test.id))
    lives = await board.lives()
    assert _categories(lab_test_items(lives)) == ["finished"]
    assert _categories(delivery_items(lives)) == ["to_return"]
    assert admin_counts(lives)["delivery"] == 1
    assert incubation_items(lives)[0][0] == "done"  # 出了专家，不用再孵化

    [expert] = (await board.lives())[0].experts
    await experts.return_to_user(str(expert.id), "admin-1")
    lives = await board.lives()
    assert _categories(delivery_items(lives)) == ["awaiting_user"]
    assert admin_counts(lives)["delivery"] == 0  # 等用户，不等我们


@pytest.mark.asyncio
async def test_a_rejected_or_failed_name_comes_back_to_incubation(parts):
    creation, _, board, runtime = parts
    request = await _submit(creation)
    await creation.accept(str(request.id), 1, all_clear(), "admin-1")
    draft = await creation.add_draft(str(request.id), manifest())
    await creation.request_user_review(str(draft.id), "admin-1")
    await creation.answer_user_review(
        OWNER, str(draft.id), UserReviewIn(ok=False, note="口径不对")
    )
    lives = await board.lives()
    assert _categories(incubation_items(lives)) == ["todo", "done"]
    assert lab_test_items(lives) == []

    again = await creation.add_draft(str(request.id), manifest())
    await creation.review_rules(str(again.id), "admin-1")
    test = await creation.start_test(
        str(again.id), budget_limit="20", timeout_seconds=600
    )
    runtime.results[test.run_id] = result(test.run_id, "failed")
    await creation.refresh(str(test.id))
    lives = await board.lives()
    assert _categories(incubation_items(lives))[0] == "todo"


@pytest.mark.asyncio
async def test_the_user_columns_and_the_inbox(parts):
    creation, experts, board, runtime = parts
    request = await _submit(creation)
    other = await _submit(creation, "someone-else")
    await creation.accept(str(request.id), 1, all_clear(), "admin-1")
    mine = await board.lives(OWNER)
    assert [life.request.id for life in mine] == [request.id]
    assert application_category(mine[0]) == "working"

    draft = await creation.add_draft(str(request.id), manifest())
    await creation.request_user_review(str(draft.id), "admin-1")
    mine = await board.lives(OWNER)
    assert [(s, k) for s, k, _, _ in inbox_items(mine, [])] == [("todo", "draft")]
    await creation.answer_user_review(OWNER, str(draft.id), UserReviewIn(ok=True))
    mine = await board.lives(OWNER)
    assert [(s, k) for s, k, _, _ in inbox_items(mine, [])] == [("done", "draft")]

    await creation.review_rules(str(draft.id), "admin-1")
    test = await creation.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600
    )
    runtime.results[test.run_id] = result(test.run_id, "passed")
    await creation.refresh(str(test.id))
    mine = await board.lives(OWNER)
    assert user_expert_items(mine) == []  # 没交回的用户看不到
    assert summary(mine[0], for_user=True)["expert"] is None
    assert summary(mine[0], for_user=False)["expert"] == {
        "version": 1,
        "stage": "to_return",
    }

    [expert] = mine[0].experts
    await experts.return_to_user(str(expert.id), "admin-1")
    mine = await board.lives(OWNER)
    assert _categories(user_expert_items(mine)) == ["new"]
    assert ("todo", "expert") in [(s, k) for s, k, _, _ in inbox_items(mine, [])]
    assert application_category(mine[0]) == "has_expert"
    await experts.decide(OWNER, str(expert.id), ExpertDecisionIn(accept=True))
    mine = await board.lives(OWNER)
    assert _categories(user_expert_items(mine)) == ["accepted"]

    # 不受理的最新版本：申请栏里是「没受理」
    await creation.accept(
        str(other.id),
        1,
        all_clear(
            checks={
                "C-L5": {"ok": False},
                "C-L2": {"ok": True},
                "C-L1": {"ok": True},
            },
            accepted=False,
            reason="投资建议类",
        ),
        "admin-1",
    )
    [theirs] = await board.lives("someone-else")
    assert application_category(theirs) == "rejected"


@pytest.mark.asyncio
async def test_the_timeline_reads_in_order_and_hides_actors_from_users(parts):
    creation, experts, board, runtime = parts
    request = await _submit(creation)
    await creation.accept(str(request.id), 1, all_clear(), "admin-1")
    draft = await creation.add_draft(str(request.id), manifest())
    await creation.review_rules(str(draft.id), "admin-1")
    test = await creation.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600, starter="admin-1"
    )
    runtime.results[test.run_id] = result(test.run_id, "passed")
    await creation.refresh(str(test.id))
    life = await board.life(str(request.id))
    admin = [e["kind"] for e in timeline(life, for_user=False)]
    assert admin[:2] == ["version", "draft"] or admin[:2] == ["version", "acceptance"]
    assert {"test_started", "test_finished", "expert"} <= set(admin)
    user = timeline(life, for_user=True)
    assert "expert" not in [e["kind"] for e in user]  # 没交回
    assert all("by" not in e for e in user)
    started = next(
        e for e in timeline(life, for_user=False) if e["kind"] == "test_started"
    )
    assert (started["by"], started["number"], started["label"]) == ("admin-1", 1, "1.1")
