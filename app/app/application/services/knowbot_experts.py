"""专家：交回、两道门、上架、停用（SDD 0037 第五、第六节）。

测试通过即是专家。上架前两道门，先用户、后运营（用户不要的不白审）：

- 交回（管理端）：把这一版通知用户，用户在 web端「审核」里看到一条待办。
- 用户同意门（web端）：要这一版 / 不要。不要的不上架。
- 运营审核门（管理端，admin、operator 都能审）：通过（定自用，或发布主体有资质的定
  公开）/ 不通过与理由。通过即交 knowmart；交付失败记下错误，可重试，不重复交
  （knowmart 按交付单号幂等）。试行期上架的（两道门之前交付）可以补一次审核记录。
- 停用（用户或管理端）：上架了的才停；先停 knowmart 那一版，停成了才记。
- 没上架的那几版（用户不要、审核不过）的结局交给 knowmart，详情页写明空号那一版
  （第三节）：有了这样的结局、或交付成功时把整份名单交过去；交不到只记一行，下一次
  再交整份。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.application.errors.exceptions import (
    AppException,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    ExpertRecord,
    KnowlabRepository,
    KnowmartClient,
    RequestRecord,
)
from app.application.services.knowbot_service import parse_id
from app.domain.knowbots.data_needs import for_knowmart
from app.domain.knowbots.lifecycle import (
    ExpertDecisionIn,
    ExpertGates,
    ExpertReviewIn,
    expert_stage,
)

log = logging.getLogger(__name__)


def stage_of(expert: ExpertRecord) -> str:
    return expert_stage(
        ExpertGates(
            returned_at=expert.returned_at,
            user_accepted=expert.user_accepted,
            review_approved=expert.review_approved,
            listed_at=expert.listed_at,
            stopped_at=expert.stopped_at,
        )
    )


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


class KnowbotExpertService:
    def __init__(
        self,
        repo: KnowlabRepository,
        knowmart: KnowmartClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.knowmart = knowmart
        self.now = now

    async def expert(self, expert_id: str) -> tuple[ExpertRecord, RequestRecord]:
        found = await self.repo.get_expert(parse_id(expert_id, "expert"))
        if found is None:
            raise NotFoundError("No such expert", code="expert_not_found")
        request = await self.repo.get_request(found.request_id)
        assert request is not None
        return found, request

    async def my_expert(
        self, owner_subject: str, expert_id: str
    ) -> tuple[ExpertRecord, RequestRecord]:
        """自己的、交回了的一版专家。没交回的、别人的与没有的一样，都是 404。"""
        found, request = await self.expert(expert_id)
        if request.owner_subject != owner_subject or found.returned_at is None:
            raise NotFoundError("No such expert", code="expert_not_found")
        return found, request

    async def _locked(self, expert_id: uuid.UUID) -> ExpertRecord:
        found = await self.repo.get_expert(expert_id, for_update=True)
        assert found is not None
        return found

    async def _reload(self, expert_id: uuid.UUID) -> ExpertRecord:
        found = await self.repo.get_expert(expert_id)
        assert found is not None
        return found

    # ---------- 交回 ----------
    async def return_to_user(self, expert_id: str, returner: str) -> ExpertRecord:
        """把这一版通知用户（用户同意门开始）。"""
        found, _ = await self.expert(expert_id)
        async with self.repo.transaction():
            current = await self._locked(found.id)
            if stage_of(current) != "to_return":
                raise _conflict("Only a new expert is returned", "expert_state")
            await self.repo.update_expert(
                current.id, returned_by=returner, returned_at=self.now()
            )
        return await self._reload(found.id)

    # ---------- 用户同意门 ----------
    async def decide(
        self, owner_subject: str, expert_id: str, body: ExpertDecisionIn
    ) -> ExpertRecord:
        """用户看过成稿与测试结果：要这一版 / 不要。"""
        found, _ = await self.my_expert(owner_subject, expert_id)
        async with self.repo.transaction():
            current = await self._locked(found.id)
            if stage_of(current) != "awaiting_user":
                raise _conflict("Already decided", "expert_state")
            await self.repo.update_expert(
                current.id,
                user_accepted=body.accept,
                user_decided_by=owner_subject,
                user_decided_at=self.now(),
            )
        if not body.accept:
            await self._tell_unlisted(found.request_id)
        return await self._reload(found.id)

    # ---------- 运营审核门 ----------
    async def review(
        self, expert_id: str, body: ExpertReviewIn, reviewer: str
    ) -> ExpertRecord:
        """运营审核：用户要了的才审；通过即交 knowmart（交付失败记下，可重试）。

        试行期上架、没审过的，补一次审核记录（不再交付）。
        """
        found, _ = await self.expert(expert_id)
        async with self.repo.transaction():
            current = await self._locked(found.id)
            stage = stage_of(current)
            backfill = (
                stage == "listed" and current.trial and current.reviewed_at is None
            )
            if stage != "to_review" and not backfill:
                raise _conflict(
                    "Only an expert the user accepted is reviewed", "expert_state"
                )
            await self.repo.update_expert(
                current.id,
                review_approved=body.approved,
                review_listing=body.listing,
                review_reason=body.reason.strip() or None,
                reviewed_by=reviewer,
                reviewed_at=self.now(),
            )
        if body.approved and not backfill:
            try:
                return await self.deliver(str(found.id))
            except AppException:
                pass  # 错误已记在专家上；审核结论照样留着，等重试
        elif not body.approved:
            await self._tell_unlisted(found.request_id)
        return await self._reload(found.id)

    async def deliver(self, expert_id: str) -> ExpertRecord:
        """把审过的这一版交给 knowmart；管理端的「重试交付」也走这里。"""
        found, request = await self.expert(expert_id)
        if stage_of(found) != "to_deliver":
            raise _conflict("Only an approved expert is delivered", "expert_state")
        draft = await self.repo.get_draft(found.draft_id)
        assert draft is not None
        try:
            delivered = await self.knowmart.deliver(
                delivery_id=f"knowlab-expert-{found.id}",
                owner_subject=request.owner_subject,
                knowbot_ref=f"knowlab-{request.id}",
                manifest=for_knowmart(draft.manifest),
                request_public=found.review_listing == "public",
            )
        except AppException as exc:
            async with self.repo.transaction():
                await self.repo.update_expert(
                    found.id, delivery_error=f"delivery failed: {exc.code}"
                )
            raise
        async with self.repo.transaction():
            current = await self._locked(found.id)
            if stage_of(current) == "to_deliver":
                await self.repo.update_expert(
                    current.id,
                    listed_at=self.now(),
                    knowmart_knowbot_id=str(delivered["knowbot_id"]),
                    digest=delivered.get("digest"),
                    delivery=delivered,
                    delivery_error=None,
                )
        await self._tell_unlisted(found.request_id)
        return await self._reload(found.id)

    async def _tell_unlisted(self, request_id: uuid.UUID) -> None:
        """把这个名称没上架那几版的结局整份交给 knowmart；交不到只记一行。"""
        request = await self.repo.get_request(request_id)
        assert request is not None
        versions = [
            {"version": str(e.version), "outcome": stage}
            for e in await self.repo.list_experts([request_id])
            if (stage := stage_of(e)) in ("declined", "rejected")
        ]
        try:
            await self.knowmart.set_unlisted(
                owner_subject=request.owner_subject,
                knowbot_ref=f"knowlab-{request.id}",
                versions=versions,
            )
        except AppException as exc:
            log.warning(
                "unlisted versions not sent request=%s code=%s", request_id, exc.code
            )

    # ---------- 停用 ----------
    async def stop(
        self,
        expert_id: str,
        reason: str,
        stopper: str,
        *,
        owner_subject: str | None = None,
    ) -> ExpertRecord:
        """停用上架了的这一版：先停 knowmart 那一版，停成了才记。

        owner_subject 给了就是用户自己停（只认自己的、交回了的）。
        """
        if owner_subject is None:
            found, request = await self.expert(expert_id)
        else:
            found, request = await self.my_expert(owner_subject, expert_id)
        if stage_of(found) != "listed":
            raise _conflict("Only a listed expert is stopped", "expert_state")
        if found.knowmart_knowbot_id:
            await self.knowmart.stop(
                owner_subject=request.owner_subject,
                knowbot_id=found.knowmart_knowbot_id,
                version=str(found.version),
            )
        async with self.repo.transaction():
            current = await self._locked(found.id)
            if stage_of(current) == "listed":
                await self.repo.update_expert(
                    current.id,
                    stopped_at=self.now(),
                    stopped_by=stopper,
                    stop_reason=reason.strip(),
                )
        log.info("knowbot expert stopped expert=%s by=%s", found.id, stopper)
        return await self._reload(found.id)
