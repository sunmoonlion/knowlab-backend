"""测试中待人决定的问题（SDD 0027 §3.4，有人值守）。

运行方跑到要人决定的地方就停下，问题记在这里。谁来答（C-L1，审读第 2 条）：工具审批
由我们的运营在管理端答；开步前的提问与要人判断的提问建出来就归用户本人，在网页端
「我在做的分身」里答。运营认为某一条只是跑法问题，要写一句理由改派给自己才能答，
答完记成「运营答的」，不算用户确认，不写回用例、不进规则出处；改派后还能转回用户。

先记下答案（同一个事务里，用户答的开步前提问同时记回用例的预设答案，重测不再问），
再交给运行方；交不到的记下错误，下次刷新时补送。运行方按问题 id 幂等，补送安全。
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    DraftRecord,
    KnowlabRepository,
    QuestionRecord,
    RequestRecord,
    RuntimeTestClient,
)
from app.domain.knowbots.test_run import CONTRACT, Answer, AnswersV1


@dataclass(frozen=True)
class AnswerIn:
    decision: str | None = None
    option_id: str | None = None
    text: str | None = None


def _parse(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise NotFoundError("No such question", code="question_not_found") from exc


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


def _check(question: QuestionRecord, given: AnswerIn) -> dict[str, Any]:
    """答案要和问题对得上：审批给决定；别的给选项（得是给出的选项）或自己写的话。"""
    if question.kind == "tool_approval":
        if given.decision not in ("approve", "deny") or given.option_id or given.text:
            raise BadRequestError(
                "An approval takes approve or deny", code="answer_invalid"
            )
        return {"decision": given.decision}
    if given.decision is not None:
        raise BadRequestError("Only approvals take a decision", code="answer_invalid")
    answer: dict[str, Any] = {}
    if given.option_id is not None:
        if given.option_id not in {o.get("id") for o in question.options}:
            raise BadRequestError("No such option", code="answer_invalid")
        answer["option_id"] = given.option_id
    text = (given.text or "").strip()
    if text:
        if not question.allow_free_text and not answer:
            raise BadRequestError("Pick one of the options", code="answer_invalid")
        if len(text) > 4000:
            raise BadRequestError("The answer is too long", code="answer_invalid")
        answer["text"] = text
    if not answer:
        raise BadRequestError("The answer is empty", code="answer_invalid")
    return answer


class KnowbotQuestionService:
    def __init__(
        self,
        repo: KnowlabRepository,
        runtimes: RuntimeTestClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.runtimes = runtimes
        self.now = now

    async def _with_context(
        self, question: QuestionRecord
    ) -> tuple[QuestionRecord, DraftRecord, RequestRecord]:
        draft = await self.repo.get_draft(question.draft_id)
        assert draft is not None
        request = await self.repo.get_request(draft.request_id)
        assert request is not None
        return question, draft, request

    # ---------- 运营（管理端） ----------
    async def operator_queue(
        self,
    ) -> list[tuple[QuestionRecord, DraftRecord, RequestRecord]]:
        """运营要处理的：还没回答、没转给用户的；以及转给用户、用户还没答的（只看）。"""
        out = []
        for question in await self.repo.list_questions(open_only=True):
            out.append(await self._with_context(question))
        return out

    async def to_user(self, question_id: str) -> None:
        """改派给自己的，转回用户。"""
        async with self.repo.transaction():
            question = await self._open(question_id)
            if question.kind == "tool_approval":
                raise _conflict("Approvals are answered by us", "question_kind")
            if question.route == "user":
                raise _conflict("Already with the user", "question_routed")
            await self.repo.update_question(
                question.id, route="user", route_reason=None
            )

    async def take(self, question_id: str, reason: str) -> None:
        """运营认为这条只是跑法问题：写一句理由，改派给自己答。"""
        reason = (reason or "").strip()
        if not reason or len(reason) > 200:
            raise BadRequestError(
                "Say in one line why this is ours to answer", code="reason_required"
            )
        async with self.repo.transaction():
            question = await self._open(question_id)
            if question.kind == "tool_approval":
                raise _conflict("Approvals are answered by us", "question_kind")
            if question.route != "user":
                raise _conflict("Already ours", "question_taken")
            await self.repo.update_question(
                question.id, route="operator", route_reason=reason
            )

    async def answer_as_operator(
        self, question_id: str, given: AnswerIn
    ) -> QuestionRecord:
        return await self._answer(question_id, given, by="operator", owner=None)

    # ---------- 用户（网页端） ----------
    async def user_queue(
        self, owner_subject: str
    ) -> list[tuple[QuestionRecord, DraftRecord, RequestRecord]]:
        out = []
        for question in await self.repo.list_questions(open_only=True):
            if question.route != "user":
                continue
            item = await self._with_context(question)
            if item[2].owner_subject == owner_subject:
                out.append(item)
        return out

    async def answer_as_user(
        self, owner_subject: str, question_id: str, given: AnswerIn
    ) -> QuestionRecord:
        return await self._answer(question_id, given, by="user", owner=owner_subject)

    async def answered(self, request_id: uuid.UUID) -> Sequence[QuestionRecord]:
        """这个分身测试中答过的问题（建下一版草稿时看：只有用户答的记进规则出处，
        运营答的另看，带改派理由）。"""
        out: list[QuestionRecord] = []
        for draft in await self.repo.list_drafts(request_id=request_id):
            out += [
                q
                for q in await self.repo.list_questions(draft_id=draft.id)
                if q.answer is not None
            ]
        return out

    # ---------- 共用 ----------
    async def _open(self, question_id: str) -> QuestionRecord:
        question = await self.repo.get_question(_parse(question_id), for_update=True)
        if question is None:
            raise NotFoundError("No such question", code="question_not_found")
        if question.answer is not None:
            raise _conflict("Already answered", "question_answered")
        return question

    async def _answer(
        self, question_id: str, given: AnswerIn, *, by: str, owner: str | None
    ) -> QuestionRecord:
        async with self.repo.transaction():
            question = await self._open(question_id)
            _, draft, request = await self._with_context(question)
            if by == "user":
                # 只答自己的、转给自己的；别人的和不存在一样
                if request.owner_subject != owner or question.route != "user":
                    raise NotFoundError("No such question", code="question_not_found")
            elif question.route == "user":
                raise _conflict(
                    "This question is the user's to answer", "question_routed"
                )
            if draft.status != "testing" or draft.run_id != question.run_id:
                raise _conflict("That test is no longer running", "question_stale")
            answer = _check(question, given)
            await self.repo.update_question(
                question.id, answer=answer, answered_by=by, answered_at=self.now()
            )
            # 只有用户答的才算他确认过：运营答的不写回用例，重测还会问
            if by == "user" and question.kind == "ask_before" and question.key:
                await self._remember(request, question, answer)
        await self._send(draft, request, [question.id])
        found = await self.repo.get_question(question.id)
        assert found is not None
        return found

    async def _remember(
        self, request: RequestRecord, question: QuestionRecord, answer: dict[str, Any]
    ) -> None:
        """开步前的提问记回用例的预设答案，重测不再问。"""
        cases = copy.deepcopy(request.cases)
        value = answer.get("option_id") or answer.get("text")
        for case in cases:
            if case.get("case_id") == question.case_id and value and question.key:
                case.setdefault("input", {}).setdefault("answers", {})[question.key] = (
                    value
                )
        await self.repo.update_request_cases(request.id, cases)

    async def _send(
        self, draft: DraftRecord, request: RequestRecord, ids: list[uuid.UUID]
    ) -> None:
        questions = [q for q in [await self.repo.get_question(i) for i in ids] if q]
        ready = [q for q in questions if q.answer is not None and q.sent_at is None]
        if not ready or draft.run_id is None:
            return
        body = AnswersV1(
            contract=CONTRACT,
            answers=tuple(
                Answer(
                    question_id=q.question_id,
                    answered_by=q.answered_by,  # type: ignore[arg-type]
                    **q.answer,  # type: ignore[arg-type]
                )
                for q in ready
            ),
        )
        try:
            await self.runtimes.answer(request.runtime, draft.run_id, body)
        except AppException as exc:
            async with self.repo.transaction():
                for q in ready:
                    await self.repo.update_question(
                        q.id, last_error=f"send failed: {exc.code}"
                    )
            return
        async with self.repo.transaction():
            for q in ready:
                await self.repo.update_question(
                    q.id, sent_at=self.now(), last_error=None
                )

    async def resend(self, draft: DraftRecord, request: RequestRecord) -> None:
        """刷新前补送答过但没送到运行方的答案。"""
        if draft.run_id is None:
            return
        pending = [
            q.id
            for q in await self.repo.list_questions(draft_id=draft.id)
            if q.answer is not None and q.sent_at is None and q.run_id == draft.run_id
        ]
        await self._send(draft, request, pending)
