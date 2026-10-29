"""AI 孵化的一轮（专家孵化方案第八、九节，第十五节第 4 步；decisions ⑮⑯）。

只有作者本人（web端）：不是自己的名称、不是这个名称的一轮，与没有的一样，都是 404。运营
看不到自助的孵化记录与缺口（SDD 0046 第四节）：管理端没有这些接口，缺口不进运营的问题
列表，按编号来答也是 404。

- 发起：作者点「AI 起草」或「让 AI 改…」一次是一轮（这个名称里第几轮），每轮都由作者点了
  才发，不自动接着跑（钱是他的）。费用上限作者设（默认 20 元）；电脑与目录不给就用上次的
  （上次自助测试或上次孵化，晚的那次），没有回 409 author_has_no_computer。同一份工作稿
  同时只许一轮没结束（409 incubation_running）。交出去的：创意最新一版拼成的种子、当前
  工作稿与每格出处、作者答过的孵化缺口（问题、提议、答复）、费用上限与超时、在哪跑。
  作者挑的数据的格式（data_formats）这一步给空：knowlab 还没有读 knowbase 资料格式的路。
- 取回：页面轮询时刷新一次（同自助测试）：问运行方，记状态、花费、委托号；成了存交回，
  核出处（incubation_rounds.review），缺口写进问题表（kind gap，归作者本人，进审核栏）。
- 取消：没结束的交运行方取消，记它回的状态。
- 接受建议（第 8.3 节）：接受 / 改了再接受 / 不要。接受照 op 改工作稿，改动到的每一格记
  一条接受记录（suggestion_ref = 孵化号:建议号），出处按引到的那一行：创意原句记
  `user_text`（idea 引用），答过的缺口记 `user_confirmed`（question 引用），提议记
  `user_confirmed`（引接受记录）；改了再接受的每格记一条编辑记录，出处 `user_text`。出处
  核不上的只能改了再接受。带 revision，对不上 409（同工作稿）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import (
    IncubationClient,
    IncubationRecord,
    KnowlabRepository,
    QuestionRecord,
    RequestRecord,
    RuntimeTestClient,
    WorkdraftRecord,
)
from app.application.services.knowbot_grants import live_incubation
from app.application.services.knowbot_service import parse_id
from app.application.services.knowbot_workdraft import (
    AUTHOR,
    KnowbotWorkdraftService,
    parse_body,
)
from app.domain.knowbots.incubation import (
    CONTRACT,
    Handback,
    IncubationRequestV1,
    IncubationResultV1,
    Suggestion,
)
from app.domain.knowbots.incubation_rounds import (
    ACTIVE_ROUNDS,
    apply_suggestion,
    related,
    review,
    seed_of,
    source_of,
)
from app.domain.knowbots.workdraft import cells, empty_body, idea_ref

DEFAULT_BUDGET = "20"  # 元（0029 第 6.5 节的提议，第 8.2 节）
DEFAULT_TIMEOUT_SECONDS = 3600  # 同自助测试的默认
PROPOSAL = "proposal"  # 缺口答「按提议」的选项编号
AS_PROPOSED = "按提议"


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


def answer_text(question: QuestionRecord) -> str:
    """作者对一个缺口的答复，交给运行方的那句话：按提议、选的那一项、自己写的。"""
    answer = question.answer or {}
    said: list[str] = []
    option = answer.get("option_id")
    if option == PROPOSAL:
        said.append(AS_PROPOSED)
    elif option:
        said.append(
            next(
                (
                    str(o.get("label"))
                    for o in question.options
                    if o.get("id") == option
                ),
                str(option),
            )
        )
    if answer.get("text"):
        said.append(str(answer["text"]))
    return "；".join(said)


def _suggestion(record: IncubationRecord, suggestion_id: str) -> Suggestion:
    handback = Handback.model_validate(record.handback or {})
    found = next((s for s in handback.suggestions if s.id == suggestion_id), None)
    if found is None:
        raise NotFoundError("No such suggestion", code="suggestion_not_found")
    return found


class KnowbotIncubationService:
    def __init__(
        self,
        repo: KnowlabRepository,
        incubator: IncubationClient,
        runtimes: RuntimeTestClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.incubator = incubator
        self.runtimes = runtimes
        self.now = now
        self.workdrafts = KnowbotWorkdraftService(repo, now=now)

    async def _request(self, owner_subject: str, request_id: str) -> RequestRecord:
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such request", code="request_not_found")
        return request

    async def mine(
        self, owner_subject: str, request_id: str, incubation_id: str
    ) -> tuple[RequestRecord, IncubationRecord]:
        """自己的名称里的一轮；别人的、别的名称的与没有的一样，都是 404。"""
        request = await self._request(owner_subject, request_id)
        found = await self.repo.get_incubation(parse_id(incubation_id, "incubation"))
        if found is None or found.request_id != request.id:
            raise NotFoundError("No such incubation", code="incubation_not_found")
        return request, found

    async def rounds(
        self, owner_subject: str, request_id: str
    ) -> Sequence[IncubationRecord]:
        """这个名称的各轮，第一轮在前。"""
        request = await self._request(owner_subject, request_id)
        return await self.repo.list_incubations(request_id=request.id)

    async def gaps(self, record: IncubationRecord) -> Sequence[QuestionRecord]:
        return await self.repo.list_gaps(incubation_id=record.id)

    # ---------- 电脑 ----------
    async def _last(self, owner_subject: str) -> dict[str, str] | None:
        """上次用的电脑与目录：上次自助测试与上次孵化里晚的那次。"""
        found: list[tuple[datetime, str, str]] = []
        test = await self.repo.last_self_test(owner_subject)
        if test is not None and test.environment_id and test.workspace_root:
            found.append((test.started_at, test.environment_id, test.workspace_root))
        for record in await self.repo.list_incubations(owner_subject=owner_subject):
            at = record.started_at or record.created_at
            if at is not None:
                found.append((at, record.environment_id, record.workspace_root))
        if not found:
            return None
        _, environment_id, workspace_root = max(found, key=lambda f: f[0])
        return {"environment_id": environment_id, "workspace_root": workspace_root}

    async def computers(self, owner_subject: str, request_id: str) -> dict[str, Any]:
        """选电脑（第 8.5 节，同自助测试）：他接入过的电脑与上次用的那台与目录。"""
        request = await self._request(owner_subject, request_id)
        return {
            "computers": await self.runtimes.computers(request.runtime, owner_subject),
            "last": await self._last(owner_subject),
        }

    async def _runner(
        self,
        request: RequestRecord,
        environment_id: str | None,
        workspace_root: str | None,
    ) -> dict[str, str]:
        if environment_id and workspace_root:
            return {"environment_id": environment_id, "workspace_root": workspace_root}
        if environment_id or workspace_root:
            raise BadRequestError(
                "Give both the computer and the folder, or neither",
                code="runner_invalid",
            )
        last = await self._last(request.owner_subject)
        if last is not None:
            computers = await self.runtimes.computers(
                request.runtime, request.owner_subject
            )
            there = next(
                (
                    c
                    for c in computers
                    if c.get("environment_id") == last["environment_id"]
                ),
                None,
            )
            if there is not None and last["workspace_root"] in list(
                there.get("roots") or []
            ):
                return last
        raise _conflict(
            "The author has no usable computer yet: pick one, or connect one in "
            "knowbench first",
            "author_has_no_computer",
        )

    # ---------- 发起 ----------
    async def _answers(self, request: RequestRecord) -> list[QuestionRecord]:
        """这个名称里作者答过的孵化缺口（第二轮起带上）。"""
        return [
            q
            for q in await self.repo.list_gaps(request_ids=[request.id])
            if q.answered_by == "user" and q.answer is not None
        ]

    async def start(
        self,
        owner_subject: str,
        request_id: str,
        *,
        action: str,
        instruction: str | None = None,
        step_id: str | None = None,
        budget_limit: str = DEFAULT_BUDGET,
        environment_id: str | None = None,
        workspace_root: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        mode: str = "self",
    ) -> IncubationRecord:
        """发起一轮：先记下（排队），再交运行方；交不出去的记失败，照样报错。"""
        request = await self._request(owner_subject, request_id)
        if action not in ("draft", "revise"):
            raise BadRequestError("Draft or revise", code="incubation_invalid")
        if (action == "revise") != bool(instruction and instruction.strip()):
            raise BadRequestError(
                "Revise says in one line what to change; draft says nothing",
                code="instruction_invalid",
            )
        if step_id is not None and action != "revise":
            raise BadRequestError(
                "Only revise names a step", code="instruction_invalid"
            )
        try:
            if Decimal(budget_limit) <= 0:
                raise InvalidOperation
        except InvalidOperation:
            raise BadRequestError(
                "The budget limit is more than zero", code="budget_invalid"
            ) from None
        if not self.incubator.configured():
            raise ServiceUnavailableError(
                "Incubation on knowbench is not configured",
                code="incubation_unconfigured",
            )
        runner = await self._runner(request, environment_id, workspace_root)
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            current = await self.repo.get_workdraft(request.id, for_update=True)
            if current is None:  # 还没存过：建一份空的，起草就从种子起
                current = await self.repo.create_workdraft(
                    request_id=request.id,
                    owner_subject=request.owner_subject,
                    body=empty_body(),
                )
            rounds = await self.repo.list_incubations(request_id=request.id)
            if any(
                r.workdraft_id == current.id and r.status in ACTIVE_ROUNDS
                for r in rounds
            ):
                raise _conflict(
                    "A round of this workdraft is still running",
                    "incubation_running",
                )
            body = parse_body(current.body)
            if step_id is not None and step_id not in {s.id for s in body.steps}:
                raise BadRequestError("No such step", code="step_not_found")
            fresh = await self.repo.get_request(request.id)
            assert fresh is not None
            version = await self.repo.get_version(fresh.id, fresh.latest_version)
            assert version is not None
            kind, text = seed_of(version.idea)
            if not text.strip():
                raise BadRequestError("The idea is empty", code="seed_empty")
            answered = await self._answers(fresh)
            incubation_id = uuid.uuid4()
            run_id = f"knowlab-incubation-{incubation_id}"
            try:
                sent = IncubationRequestV1.model_validate(
                    {
                        "contract": CONTRACT,
                        "incubation_id": run_id,
                        "owner_subject": owner_subject,
                        "action": action,
                        "instruction": instruction,
                        "step_id": step_id,
                        "seed": {"kind": kind, "text": text},
                        **(
                            {"reference": fresh.reference_answer}
                            if fresh.reference_answer
                            else {}
                        ),
                        "workdraft": {
                            "body": current.body,
                            "sources": {
                                path: {"kind": src.get("kind"), "ref": src.get("ref")}
                                for path, src in current.sources.items()
                                if isinstance(src, dict)
                            },
                        },
                        "answers": [
                            {
                                "id": str(q.id),
                                "question": q.text,
                                "proposal": q.proposal or "",
                                "answer": answer_text(q),
                            }
                            for q in answered
                        ],
                        # 作者挑的数据的格式：knowlab 还没有读 knowbase 资料格式
                        # 的路，先空着
                        "data_formats": [],
                        "budget": {"limit": budget_limit},
                        "timeout_seconds": timeout_seconds,
                        "runner": {"kind": "user", **runner},
                    }
                )
            except ValidationError as exc:
                where = sorted(
                    {".".join(str(p) for p in e["loc"]) for e in exc.errors()}
                )
                raise BadRequestError(
                    "This round cannot be sent: " + ", ".join(where[:8]),
                    code="incubation_invalid",
                ) from None
            record = await self.repo.create_incubation(
                IncubationRecord(
                    id=incubation_id,
                    request_id=fresh.id,
                    workdraft_id=current.id,
                    owner_subject=owner_subject,
                    round=max((r.round for r in rounds), default=0) + 1,
                    action=action,
                    instruction=instruction,
                    step_id=step_id,
                    status="queued",
                    run_id=run_id,
                    budget_limit=budget_limit,
                    timeout_seconds=timeout_seconds,
                    environment_id=runner["environment_id"],
                    workspace_root=runner["workspace_root"],
                    request_version=version.version,
                    seed={"kind": kind, "text": text},
                    workdraft_revision=current.revision,
                    answer_ids=[str(q.id) for q in answered],
                    started_at=self.now(),
                    mode=mode,
                )
            )
        try:
            result = await self.incubator.start(sent)
        except AppException as exc:
            await self._end(record.id, f"start failed: {exc.code}")
            raise
        await self._take(record.id, result)
        found = await self.repo.get_incubation(record.id)
        assert found is not None
        return found

    async def operator_start(
        self,
        request_id: str,
        *,
        action: str,
        instruction: str | None = None,
        step_id: str | None = None,
    ) -> IncubationRecord:
        """平台孵化时运营发起一轮（专家孵化方案第十二节）：仍在作者的账号下、作者上次用的
        电脑上、用作者的 key 跑（decisions ⑯），上限是作者请平台孵化时设的；记平台模式。
        建议照旧直接给作者处理，运营看得到、可以改成自己的提议再交。"""
        request = await self.repo.get_request(parse_id(request_id, "request"))
        grant = (
            None
            if request is None
            else live_incubation(await self.repo.list_grants([request.id]), self.now())
        )
        if request is None or grant is None or grant.status != "active":
            raise NotFoundError("No such request", code="request_not_found")
        return await self.start(
            request.owner_subject,
            request_id,
            action=action,
            instruction=instruction,
            step_id=step_id,
            budget_limit=f"{grant.budget_limit:.2f}",
            mode="platform",
        )

    async def operator_rounds(self, request_id: str) -> Sequence[IncubationRecord]:
        """孵化授权已生效时运营看的各轮：只有平台模式的（作者自助的那几轮看不到）。"""
        request = await self.repo.get_request(parse_id(request_id, "request"))
        grant = (
            None
            if request is None
            else live_incubation(await self.repo.list_grants([request.id]), self.now())
        )
        if request is None or grant is None or grant.status != "active":
            raise NotFoundError("No such request", code="request_not_found")
        return [
            r
            for r in await self.repo.list_incubations(request_id=request.id)
            if r.mode == "platform"
        ]

    async def _end(self, incubation_id: uuid.UUID, error: str) -> None:
        async with self.repo.transaction():
            record = await self.repo.get_incubation(incubation_id, for_update=True)
            if record is None or record.status not in ACTIVE_ROUNDS:
                return
            await self.repo.update_incubation(
                record.id, status="failed", error=error, finished_at=self.now()
            )

    # ---------- 取回、取消 ----------
    async def _take(self, incubation_id: uuid.UUID, result: IncubationResultV1) -> None:
        """记下运行方说的：状态、花费、委托号、在等什么；成了的存交回、核出处、缺口进问题
        表。已经结束了的不再改（旧结果不算数）。"""
        async with self.repo.transaction():
            record = await self.repo.get_incubation(incubation_id, for_update=True)
            if record is None or record.status not in ACTIVE_ROUNDS:
                return
            if result.incubation_id != record.run_id:
                return
            changes: dict[str, Any] = {
                "status": result.status,
                "waiting_for": result.waiting_for,
                "error": result.error,
            }
            if result.cost is not None:
                changes["spent"] = result.cost.spent
            if result.task_id is not None:
                changes["task_id"] = result.task_id
            if result.final:
                changes["finished_at"] = self.now()
            handback = result.handback
            if handback is not None:
                changes["handback"] = handback.model_dump(mode="json")
                changes["review"] = review(
                    handback,
                    seed_text=str(record.seed.get("text") or ""),
                    answer_ids=set(record.answer_ids),
                )
            await self.repo.update_incubation(record.id, **changes)
            if handback is not None and handback.gaps:
                asked = (result.finished_at or self.now().isoformat())[:40]
                await self.repo.add_gaps(
                    record,
                    [
                        {
                            "question_id": g.id,
                            "text": g.question,
                            "proposal": g.proposal,
                            "options": [
                                {"id": f"o{n}", "label": label}
                                for n, label in enumerate(g.options, start=1)
                            ],
                            "asked_at": asked,
                        }
                        for g in handback.gaps
                    ],
                )

    async def refresh(
        self, owner_subject: str, request_id: str, incubation_id: str
    ) -> IncubationRecord:
        """页面轮询：没结束的问一次运行方。"""
        _, record = await self.mine(owner_subject, request_id, incubation_id)
        if record.status in ACTIVE_ROUNDS:
            await self._take(record.id, await self.incubator.status(record.run_id))
        found = await self.repo.get_incubation(record.id)
        assert found is not None
        return found

    async def cancel(
        self, owner_subject: str, request_id: str, incubation_id: str
    ) -> IncubationRecord:
        """取消没结束的一轮；结束了的照原样。"""
        _, record = await self.mine(owner_subject, request_id, incubation_id)
        if record.status in ACTIVE_ROUNDS:
            await self._take(record.id, await self.incubator.cancel(record.run_id))
        found = await self.repo.get_incubation(record.id)
        assert found is not None
        return found

    # ---------- 接受建议 ----------
    async def decide(
        self,
        owner_subject: str,
        request_id: str,
        incubation_id: str,
        suggestion_id: str,
        *,
        action: str,
        revision: int | None = None,
        value: Any = None,
    ) -> tuple[WorkdraftRecord | None, IncubationRecord]:
        """对一条建议：accept 原样接受、accept_edited 改了再接受（value 是改后的）、
        reject 不要。回（工作稿，这一轮）；不要的不碰工作稿，工作稿回 None。"""
        request, record = await self.mine(owner_subject, request_id, incubation_id)
        if record.status != "succeeded":
            raise _conflict("This round handed nothing back", "incubation_state")
        suggestion = _suggestion(record, suggestion_id)
        if action not in ("accept", "accept_edited", "reject"):
            raise BadRequestError("Unknown action", code="decision_invalid")
        stored: WorkdraftRecord | None = None
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            fresh = await self.repo.get_incubation(record.id, for_update=True)
            assert fresh is not None
            if suggestion.id in fresh.decisions:
                raise _conflict(
                    "This suggestion was already decided", "suggestion_decided"
                )
            records: list[str] = []
            if action != "reject":
                if revision is None:
                    raise BadRequestError(
                        "Say which revision you read", code="workdraft_invalid"
                    )
                stored, records = await self._apply(
                    request, fresh, suggestion, action, revision, value
                )
            decisions = dict(fresh.decisions)
            decisions[suggestion.id] = {
                "state": "accepted"
                if action == "accept"
                else ("accepted_edited" if action == "accept_edited" else "rejected"),
                "at": self.now().isoformat(),
                "records": records,
            }
            await self.repo.update_incubation(fresh.id, decisions=decisions)
        found = await self.repo.get_incubation(record.id)
        assert found is not None
        return stored, found

    async def _apply(
        self,
        request: RequestRecord,
        record: IncubationRecord,
        suggestion: Suggestion,
        action: str,
        revision: int,
        value: Any,
    ) -> tuple[WorkdraftRecord, list[str]]:
        checked = (record.review or {}).get(suggestion.id) or {}
        if action == "accept" and not checked.get("verified"):
            raise _conflict(
                "Its source does not check out: edit it, then accept",
                "suggestion_unverified",
            )
        if action == "accept_edited":
            if suggestion.op in ("set", "add") and value is None:
                raise BadRequestError(
                    "Give the edited value", code="suggestion_value_missing"
                )
            if suggestion.op == "remove":
                value = None
            elif suggestion.op == "move" and value is None:
                value = suggestion.value
        else:
            value = suggestion.value
        current = await self.workdrafts.locked(request, revision)
        try:
            doc = apply_suggestion(current.body, suggestion.path, suggestion.op, value)
        except ValueError as exc:
            raise BadRequestError(
                f"Cannot apply it to the workdraft: {exc}"[:500],
                code="suggestion_not_applicable",
            ) from None
        body = parse_body(doc)
        before, after = cells(parse_body(current.body)), cells(body)
        sources = {p: s for p, s in current.sources.items() if p in after}
        handback = Handback.model_validate(record.handback or {})
        rows = related(handback.sources, suggestion.path)
        made: list[str] = []
        for path, text in after.items():
            if before.get(path) == text and path in sources:
                continue
            if action == "accept_edited":
                entry = await self.repo.add_workdraft_entry(
                    workdraft_id=current.id,
                    kind="edit",
                    actor_subject=record.owner_subject,
                    actor_role=AUTHOR,
                    path=path,
                    text=text,
                )
                sources[path] = {"kind": "user_text", "ref": f"record:{entry.id}"}
                made.append(str(entry.id))
                continue
            entry = await self.repo.add_workdraft_entry(
                workdraft_id=current.id,
                kind="accept",
                actor_subject=record.owner_subject,
                actor_role=AUTHOR,
                path=path,
                text=text,
                suggestion_ref=f"{record.id}:{suggestion.id}",
            )
            made.append(str(entry.id))
            row = source_of(rows, path)
            if row is not None and row.kind == "idea":
                ref = idea_ref(
                    record.request_version,
                    str(record.seed.get("text") or ""),
                    row.quote or "",
                )
                if ref is None:  # 核过了才到这里；对不上就当没核过
                    raise _conflict(
                        "Its source does not check out: edit it, then accept",
                        "suggestion_unverified",
                    )
                sources[path] = {"kind": "user_text", "ref": ref}
            elif row is not None and row.kind == "answer":
                sources[path] = {"kind": "user_confirmed", "ref": f"question:{row.ref}"}
            else:
                sources[path] = {"kind": "user_confirmed", "ref": f"record:{entry.id}"}
        return await self.workdrafts.store(current, body, sources), made
