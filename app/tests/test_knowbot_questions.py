"""有人值守的测试（SDD 0027 §3.4）。

问题记下、谁来答、交回运行方、记回用例、等人不计超时。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_questions import AnswerIn, KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.test_run import CONTRACT, Answer, RunResultV1
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    manifest,
    request_body,
    result,
    waiting,
)


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 17, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
async def testing():
    repo, runtime, clock = MemoryKnowlab(), FakeRuntime(), Clock()
    asks = KnowbotQuestionService(repo, runtime, now=clock)
    svc = KnowbotCreationService(
        repo, runtime, FakeKnowmart(), now=clock, on_refresh=asks.resend
    )
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[run.run_id] = waiting(run.run_id)
    await svc.refresh(str(draft.id))
    by_key = {q.question_id: q for q in repo.questions.values()}
    return svc, asks, repo, runtime, clock, draft, run, by_key


async def test_waiting_questions_are_recorded_once(testing) -> None:
    svc, asks, repo, _, _, draft, run, by_key = testing
    assert set(by_key) == {"q-approve", "q-scope", "q-judge"}
    assert repo.drafts[draft.id].status == "testing"
    assert repo.drafts[draft.id].report["status"] == "waiting"
    await svc.refresh(str(draft.id))
    assert len(repo.questions) == 3
    assert len(await asks.operator_queue()) == 3


async def test_we_answer_approvals_and_the_answer_reaches_the_runtime(testing) -> None:
    _, asks, _, runtime, _, _, run, by_key = testing
    with pytest.raises(BadRequestError):
        await asks.answer_as_operator(str(by_key["q-approve"].id), AnswerIn(text="好"))
    done = await asks.answer_as_operator(
        str(by_key["q-approve"].id), AnswerIn(decision="approve")
    )
    assert done.sent_at is not None and done.answered_by == "operator"
    [(run_id, body)] = runtime.answers
    assert run_id == run.run_id
    assert body.answers[0].question_id == "q-approve"
    assert body.answers[0].decision == "approve"
    with pytest.raises(ConcurrencyConflictError):
        await asks.answer_as_operator(
            str(by_key["q-approve"].id), AnswerIn(decision="deny")
        )


async def test_investment_judgements_go_to_the_user_only(testing) -> None:
    _, asks, _, runtime, _, _, _, by_key = testing
    judge = str(by_key["q-judge"].id)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await asks.to_user(str(by_key["q-approve"].id))
    assert raised.value.code == "question_kind"
    await asks.to_user(judge)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await asks.answer_as_operator(judge, AnswerIn(text="按营业收入"))
    assert raised.value.code == "question_routed"
    assert [q.question_id for q, _, _ in await asks.user_queue(OWNER)] == ["q-judge"]
    assert await asks.user_queue("someone-else") == []
    with pytest.raises(NotFoundError):
        await asks.answer_as_user("someone-else", judge, AnswerIn(text="x"))
    done = await asks.answer_as_user(
        OWNER, judge, AnswerIn(text="按营业收入减营业成本")
    )
    assert done.answered_by == "user" and done.sent_at is not None
    assert runtime.answers[-1][1].answers[0].answered_by == "user"


async def test_ask_before_answers_are_remembered_on_the_case(testing) -> None:
    _, asks, repo, _, _, draft, _, by_key = testing
    with pytest.raises(BadRequestError):
        await asks.answer_as_operator(
            str(by_key["q-scope"].id), AnswerIn(option_id="9y")
        )
    await asks.answer_as_operator(str(by_key["q-scope"].id), AnswerIn(option_id="3y"))
    request = repo.requests[draft.request_id]
    assert request.cases[0]["input"]["answers"] == {"period": "3y"}  # 重测不再问
    answered = await asks.answered(request.id)
    assert [q.question_id for q in answered] == ["q-scope"]


async def test_unsent_answers_are_resent_on_refresh(testing) -> None:
    svc, asks, repo, runtime, _, draft, _, by_key = testing
    runtime.fail_answer = True
    kept = await asks.answer_as_operator(
        str(by_key["q-approve"].id), AnswerIn(decision="deny")
    )
    assert (
        kept.sent_at is None
        and kept.last_error == "send failed: investment_unavailable"
    )
    runtime.fail_answer = False
    await svc.refresh(str(draft.id))
    assert repo.questions[kept.id].sent_at is not None
    assert runtime.answers[-1][1].answers[0].decision == "deny"


async def test_answers_for_a_returned_test_are_refused(testing) -> None:
    svc, asks, _, _, clock, draft, _, by_key = testing
    # 等人回答的时间不计超时：超时 + 已等 + 最长等待 + 宽限之前都不算卡住
    clock.at += timedelta(seconds=600 + 600 + 6 * 60)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.reset_stuck(str(draft.id))
    assert raised.value.code == "not_stuck"
    clock.at += timedelta(days=3)
    await svc.reset_stuck(str(draft.id))
    with pytest.raises(ConcurrencyConflictError) as raised:
        await asks.answer_as_operator(
            str(by_key["q-approve"].id), AnswerIn(decision="approve")
        )
    assert raised.value.code == "question_stale"


def test_the_contract_ties_waiting_to_questions() -> None:
    with pytest.raises(ValidationError):
        RunResultV1.model_validate(
            {"contract": CONTRACT, "run_id": "run-00001", "status": "waiting"}
        )
    with pytest.raises(ValidationError):
        result("run-00001", "running", questions=waiting("run-00001").questions)
    with pytest.raises(ValidationError):
        Answer(question_id="q", answered_by="user")
    with pytest.raises(ValidationError):
        Answer(question_id="q", decision="approve", text="x", answered_by="operator")
    Answer(question_id="q", option_id="other", text="自己写的", answered_by="user")
