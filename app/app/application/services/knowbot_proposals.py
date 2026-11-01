"""运营对工作稿的提议（专家孵化方案第十二节；decisions 10-29「孵化第 5 步的三件」）。

平台孵化时运营在管理端用同一个编辑器改工作稿，改的都是提议：一条一格，形状同 AI 的
建议（路径、set / add / remove / move、值、理由）。作者在 web端逐条处理——接受、改了再
接受、不要；接受了才进稿，记成作者的接受记录，出处照旧只认作者（第九节）。

- 运营要这个名称的孵化授权已生效（受理过、没撤、没过期）才看得到工作稿、能提议。
- 提议时按现稿试一遍，套不上的当场拒；作者处理时再按那时的现稿套一遍。
- 运营可以收回还没处理的提议；作者点「我自己来」时没处理的一律作废
  （KnowbotPlatformService.take_back 调 `void_pending`）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    KnowlabRepository,
    ProposalRecord,
    RequestRecord,
    WorkdraftRecord,
)
from app.application.services.knowbot_service import parse_id
from app.application.services.knowbot_workdraft import (
    AUTHOR,
    KnowbotWorkdraftService,
    parse_body,
)
from app.domain.knowbots.incubation import Suggestion
from app.domain.knowbots.incubation_rounds import apply_suggestion
from app.domain.knowbots.workdraft import cells, empty_body

MAX_PROPOSALS = 50


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


def _checked(item: dict[str, Any]) -> Suggestion:
    """一条提议的形状照 AI 的建议核（路径、动作与值对得上、不碰工程层）。"""
    try:
        return Suggestion.model_validate({"id": "p1", **item})
    except ValidationError as exc:
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise BadRequestError(
            "This proposal is malformed: " + ", ".join(where[:8]),
            code="proposal_invalid",
        ) from None


class KnowbotProposalService:
    def __init__(
        self,
        repo: KnowlabRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.now = now
        self.workdrafts = KnowbotWorkdraftService(repo, now=now)

    # ---------- 运营 ----------
    async def platform_request(self, request_id: str) -> RequestRecord:
        """孵化授权已生效的名称；其余与没有的一样，都是 404。"""
        return await self.workdrafts.platform_request(request_id)

    async def operator_view(
        self, request_id: str
    ) -> tuple[RequestRecord, WorkdraftRecord | None, Sequence[ProposalRecord]]:
        request = await self.platform_request(request_id)
        return (
            request,
            await self.repo.get_workdraft(request.id),
            await self.repo.list_proposals(request.id),
        )

    async def propose(
        self, request_id: str, operator: str, items: Sequence[dict[str, Any]]
    ) -> list[ProposalRecord]:
        """一次提几条；按现稿依次试套，套不上的整批拒。"""
        if not items or len(items) > MAX_PROPOSALS:
            raise BadRequestError(
                f"Propose 1 to {MAX_PROPOSALS} at a time", code="proposal_invalid"
            )
        request = await self.platform_request(request_id)
        checked = [_checked(item) for item in items]
        current = await self.repo.get_workdraft(request.id)
        doc = empty_body() if current is None else current.body
        for s in checked:
            try:
                doc = apply_suggestion(doc, s.path, s.op, s.value)
            except ValueError as exc:
                raise BadRequestError(
                    f"Cannot apply {s.path[:80]}: {exc}"[:500],
                    code="proposal_not_applicable",
                ) from None
        try:
            parse_body(doc)
        except (ValidationError, ValueError) as exc:
            raise BadRequestError(
                f"The workdraft would not hold: {exc}"[:500],
                code="proposal_not_applicable",
            ) from None
        now = self.now()
        records = [
            ProposalRecord(
                id=uuid.uuid4(),
                request_id=request.id,
                path=s.path,
                op=s.op,
                value=s.value,
                reason=s.reason,
                proposed_by=operator,
                proposed_at=now,
            )
            for s in checked
        ]
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            await self.platform_request(request_id)
            await self.repo.add_proposals(records)
        return records

    async def withdraw(self, request_id: str, proposal_id: str) -> None:
        """运营收回还没处理的提议。"""
        request = await self.platform_request(request_id)
        async with self.repo.transaction():
            found = await self._proposal(request, proposal_id, for_update=True)
            if found.state != "pending":
                raise _conflict("Already decided", "proposal_decided")
            await self.repo.update_proposal(
                found.id, state="withdrawn", decided_at=self.now()
            )

    # ---------- 作者 ----------
    async def mine(
        self, owner_subject: str, request_id: str
    ) -> tuple[RequestRecord, Sequence[ProposalRecord]]:
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such request", code="request_not_found")
        return request, await self.repo.list_proposals(request.id)

    async def decide(
        self,
        owner_subject: str,
        request_id: str,
        proposal_id: str,
        *,
        action: str,
        revision: int | None = None,
        value: Any = None,
    ) -> tuple[WorkdraftRecord | None, ProposalRecord]:
        """accept 原样接受、accept_edited 改了再接受（value 是改后的）、reject 不要。
        回（工作稿，这条提议）；不要的不碰工作稿，工作稿回 None。"""
        request, _ = await self.mine(owner_subject, request_id)
        if action not in ("accept", "accept_edited", "reject"):
            raise BadRequestError("Unknown action", code="decision_invalid")
        stored: WorkdraftRecord | None = None
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            found = await self._proposal(request, proposal_id, for_update=True)
            if found.state != "pending":
                raise _conflict("Already decided", "proposal_decided")
            records: list[str] = []
            if action != "reject":
                if revision is None:
                    raise BadRequestError(
                        "Say which revision you read", code="workdraft_invalid"
                    )
                stored, records = await self._apply(
                    request, found, action, revision, value
                )
            await self.repo.update_proposal(
                found.id,
                state="accepted"
                if action == "accept"
                else ("accepted_edited" if action == "accept_edited" else "rejected"),
                decided_at=self.now(),
                records=records,
            )
        decided = await self.repo.get_proposal(found.id)
        assert decided is not None
        return stored, decided

    async def _apply(
        self,
        request: RequestRecord,
        proposal: ProposalRecord,
        action: str,
        revision: int,
        value: Any,
    ) -> tuple[WorkdraftRecord, list[str]]:
        if action == "accept_edited":
            if proposal.op in ("set", "add") and value is None:
                raise BadRequestError(
                    "Give the edited value", code="suggestion_value_missing"
                )
            if proposal.op == "remove":
                value = None
            elif proposal.op == "move" and value is None:
                value = proposal.value
            else:
                _checked(
                    {
                        "path": proposal.path,
                        "op": proposal.op,
                        "value": value,
                        "reason": proposal.reason,
                    }
                )
        else:
            value = proposal.value
        current = await self.workdrafts.locked(request, revision)
        try:
            doc = apply_suggestion(current.body, proposal.path, proposal.op, value)
            body = parse_body(doc)
        except (ValidationError, ValueError) as exc:
            raise BadRequestError(
                f"Cannot apply it to the workdraft: {exc}"[:500],
                code="suggestion_not_applicable",
            ) from None
        before, after = cells(parse_body(current.body)), cells(body)
        sources = {p: s for p, s in current.sources.items() if p in after}
        made: list[str] = []
        for path, text in after.items():
            if before.get(path) == text and path in sources:
                continue
            if action == "accept_edited":
                # 改了再接受：改后的字是作者自己的
                entry = await self.repo.add_workdraft_entry(
                    workdraft_id=current.id,
                    kind="edit",
                    actor_subject=request.owner_subject,
                    actor_role=AUTHOR,
                    path=path,
                    text=text,
                )
                sources[path] = {"kind": "user_text", "ref": f"record:{entry.id}"}
            else:
                entry = await self.repo.add_workdraft_entry(
                    workdraft_id=current.id,
                    kind="accept",
                    actor_subject=request.owner_subject,
                    actor_role=AUTHOR,
                    path=path,
                    text=text,
                    suggestion_ref=f"proposal:{proposal.id}",
                )
                sources[path] = {"kind": "user_confirmed", "ref": f"record:{entry.id}"}
            made.append(str(entry.id))
        return await self.workdrafts.store(current, body, sources), made

    # ---------- 共用 ----------
    async def void_pending(self, request_id: uuid.UUID) -> None:
        """作者点「我自己来」：没处理的提议一律作废（在调用方的事务里）。"""
        now = self.now()
        for p in await self.repo.list_proposals(request_id):
            if p.state == "pending":
                await self.repo.update_proposal(p.id, state="void", decided_at=now)

    async def _proposal(
        self, request: RequestRecord, proposal_id: str, *, for_update: bool = False
    ) -> ProposalRecord:
        found = await self.repo.get_proposal(
            parse_id(proposal_id, "proposal"), for_update=for_update
        )
        if found is None or found.request_id != request.id:
            raise NotFoundError("No such proposal", code="proposal_not_found")
        return found
