"""专家：交回、作者不要、没上架的结局、停用（SDD 0037 第五、第六节；0046 第五节）。

测试通过即是专家。上架前两道门，先用户、后运营（用户不要的不白审）：

- 交回（管理端）：平台孵化、平台测的那一版先交回作者，作者在 web端「待办」里看到一条。
  自助测的、自助孵化的不用交回，作者直接看得到（0046 第五节「提交交付即过」）。
- 用户同意门：作者「提交交付」（选自用或公开、授权审核员）就是要这一版，在
  knowbot_deliveries；不要的在这里记下，不上架。
- 运营审核门与交 knowmart：都在 knowbot_deliveries（自用轻审能自动查的自动查，公开
  完整审核，自助测的还要平台重跑）。
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
    DeliveryRecord,
    ExpertRecord,
    KnowbaseDeliveryClient,
    KnowlabRepository,
    KnowmartClient,
    RequestRecord,
)
from app.application.services.knowbot_service import KnowbotCreationService, parse_id
from app.domain.knowbots.lifecycle import ExpertGates, expert_stage

log = logging.getLogger(__name__)


def stage_of(expert: ExpertRecord) -> str:
    return expert_stage(
        ExpertGates(
            returned_at=expert.returned_at,
            user_accepted=expert.user_accepted,
            review_approved=expert.review_approved,
            listed_at=expert.listed_at,
            stopped_at=expert.stopped_at,
            needs_return=expert.needs_return,
        )
    )


def shown_to_author(expert: ExpertRecord) -> bool:
    """作者看得到这一版：不用交回的（自助测、自助孵化的）一出就看得到，平台孵化、平台测
    的交回了才看得到（SDD 0046 第五节）。"""
    return not expert.needs_return or expert.returned_at is not None


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


class KnowbotExpertService:
    def __init__(
        self,
        repo: KnowlabRepository,
        knowmart: KnowmartClient,
        *,
        knowbase: KnowbaseDeliveryClient | None = None,
        creation: KnowbotCreationService | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.knowmart = knowmart
        self.knowbase = knowbase
        self.creation = creation
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
        """自己的、看得到的一版专家。要交回而没交回的、别人的与没有的一样，都是 404。"""
        found, request = await self.expert(expert_id)
        if request.owner_subject != owner_subject or not shown_to_author(found):
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

    # ---------- 用户同意门：不要 ----------
    async def decline(self, owner_subject: str, expert_id: str) -> ExpertRecord:
        """作者不要这一版：不上架，结局交给 knowmart（要的走「提交交付」）。"""
        found, _ = await self.my_expert(owner_subject, expert_id)
        async with self.repo.transaction():
            current = await self._locked(found.id)
            if stage_of(current) != "awaiting_user":
                raise _conflict("Already decided", "expert_state")
            await self.repo.update_expert(
                current.id,
                user_accepted=False,
                user_decided_by=owner_subject,
                user_decided_at=self.now(),
            )
        await self.tell_unlisted(found.request_id)
        return await self._reload(found.id)

    async def tell_unlisted(self, request_id: uuid.UUID) -> None:
        """把这个名称没上架、结局还没送到的那几版逐条交给 knowmart（Astra 评审 F08）。

        按版本送，结局是终态，重发、乱序都无害；送到一条记一条，交不到的留着，由
        定时任务 resend_unlisted 补发。
        """
        for expert in await self.repo.list_experts([request_id]):
            if expert.unlisted_sent_at is None:
                await self._send_unlisted(expert)

    async def resend_unlisted(self, limit: int = 100) -> int:
        """定时补发：没上架、结局还没送到 knowmart 的，逐条再送；回送到了几条。"""
        sent = 0
        for expert in await self.repo.experts_unlisted_unsent(limit):
            sent += await self._send_unlisted(expert)
        return sent

    async def _send_unlisted(self, expert: ExpertRecord) -> bool:
        outcome = stage_of(expert)
        if outcome not in ("declined", "rejected"):
            return False
        request = await self.repo.get_request(expert.request_id)
        assert request is not None
        try:
            await self.knowmart.set_unlisted(
                owner_subject=request.owner_subject,
                knowbot_ref=f"knowlab-{request.id}",
                version=str(expert.version),
                outcome=outcome,
            )
        except AppException as exc:
            log.warning(
                "unlisted outcome not sent expert=%s code=%s", expert.id, exc.code
            )
            return False
        async with self.repo.transaction():
            await self.repo.update_expert(expert.id, unlisted_sent_at=self.now())
        return True

    # ---------- knowmart 那头的状态（0043 第 4.3 节） ----------
    async def sync_market(self, limit: int = 100) -> int:
        """后台每分钟：上架了、没停用的各版，取回 knowmart 那头现在怎样（所绑数据失效
        转「待处理」与原因），记在专家上，作者在专家栏看得到。最久没取的先取（轮换），
        一个名称一次取，取不到下一分钟再取。回变了几版。"""
        listed = sorted(
            (
                e
                for e in await self.repo.list_experts()
                if e.listed_at is not None and e.stopped_at is None
            ),
            # 轮换：最久没取的先取，一轮最多 limit 位，下一轮接着取后面的
            key=lambda e: str((e.delivery or {}).get("market_checked_at") or ""),
        )[:limit]
        by_request: dict[uuid.UUID, list[ExpertRecord]] = {}
        for expert in listed:
            by_request.setdefault(expert.request_id, []).append(expert)
        changed = 0
        for request_id, experts in by_request.items():
            request = await self.repo.get_request(request_id)
            assert request is not None
            try:
                versions = await self.knowmart.versions(
                    owner_subject=request.owner_subject,
                    knowbot_ref=f"knowlab-{request.id}",
                )
            except AppException as exc:
                log.warning(
                    "knowmart state not read request=%s code=%s", request_id, exc.code
                )
                continue
            by_version = {str(v.get("version")): v for v in versions}
            for expert in experts:
                found = by_version.get(str(expert.version))
                if found is None:
                    continue
                state = {
                    "version_status": found.get("status"),
                    "held_reason": found.get("held_reason"),
                }
                async with self.repo.transaction():
                    locked = await self._locked(expert.id)
                    current = dict(locked.delivery or {})
                    moved = any(current.get(k) != v for k, v in state.items())
                    await self.repo.update_expert(
                        expert.id,
                        delivery={
                            **current,
                            **state,
                            "market_checked_at": self.now().isoformat(),
                        },
                    )
                changed += moved
        return changed

    # ---------- 停用 ----------
    async def stop(
        self,
        expert_id: str,
        reason: str,
        stopper: str,
        *,
        owner_subject: str | None = None,
    ) -> ExpertRecord:
        """停用上架了的这一版：先停 knowmart 那一版，停成了才记；有在走的转公开交付一并
        按撤回处理（停重跑、关授权、标 withdrawn）。

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
        now = self.now()
        withdrawn: list[DeliveryRecord] = []
        async with self.repo.transaction():
            current = await self._locked(found.id)
            if stage_of(current) == "listed":
                await self.repo.update_expert(
                    current.id,
                    stopped_at=now,
                    stopped_by=stopper,
                    stop_reason=reason.strip(),
                )
            for d in await self.repo.list_deliveries(expert_id=current.id):
                if d.status == "reviewing" or (
                    d.status == "approved" and d.delivered_at is None
                ):
                    locked_d = await self.repo.get_delivery(d.id, for_update=True)
                    if locked_d is not None and (
                        locked_d.status == "reviewing"
                        or (
                            locked_d.status == "approved"
                            and locked_d.delivered_at is None
                        )
                    ):
                        await self.repo.update_delivery(
                            locked_d.id,
                            status="withdrawn",
                            withdrawn_by=stopper,
                            withdrawn_at=now,
                            withdraw_reason=f"专家已停用：{reason.strip()}"[:1000],
                        )
                        withdrawn.append(locked_d)
        for d in withdrawn:
            if self.creation is not None and d.rerun_test_id is not None:
                await self.creation.stop_rerun(d.rerun_test_id, "expert stopped")
            if self.knowbase is not None:
                kinds = [
                    k for k in ("review", "rerun") if int(d.consents.get(k) or 0) > 0
                ]
                ended = True
                for kind in kinds:
                    try:
                        await self.knowbase.end_platform(
                            subject=request.owner_subject,
                            kind=kind,
                            ref=str(d.id),
                            reason="cancelled",
                        )
                    except AppException as exc:
                        log.warning(
                            "delivery consents not ended on stop delivery=%s code=%s",
                            d.id,
                            exc.code,
                        )
                        ended = False
                        break
                if ended:
                    async with self.repo.transaction():
                        await self.repo.update_delivery(d.id, consent_ended_at=now)
        log.info("knowbot expert stopped expert=%s by=%s", found.id, stopper)
        return await self._reload(found.id)
