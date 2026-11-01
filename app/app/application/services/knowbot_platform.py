"""孵化与测试在自助、平台之间切换（SDD 0046 第二、四节；专家孵化方案第十五节第 5 步）。

作者一侧（web端）：
- 「请平台孵化」：记一张孵化授权（pending），名称转平台；这一版创意正式受理后授权生效。
- 「我自己来」：孵化授权立即撤销，名称转自助，运营立即看不到；最新一版没有受理单的补记
  「自助（不受理）」。
- 续期：生效了的孵化授权最长 30 天，到期（或快到期）作者一键续到 30 天后。
- 「交平台测」：自助孵化的准专家交平台测这一次（轻受理），费用上限作者设。

平台一侧（管理端）：
- 正式受理写在 KnowbotCreationService.accept：受理了让 pending 的孵化授权生效，不受理
  就结束它、名称转回自助。
- 交还：平台孵化做完，把草稿交还作者，孵化授权结束、名称转自助。
- 轻受理：接或不接这一次平台测试。

运营看得到什么一律按还有效的授权算（knowbot_board.operator_view）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.application.errors.exceptions import (
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import GrantRecord, KnowlabRepository
from app.application.services.knowbot_grants import live_incubation, self_acceptance
from app.application.services.knowbot_service import parse_id
from app.domain.knowbots.platform_grants import (
    LightAcceptanceIn,
    PlatformIncubationIn,
    PlatformTestIn,
    expiry,
)


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


class KnowbotPlatformService:
    def __init__(
        self,
        repo: KnowlabRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.now = now

    # ---------- 作者 ----------
    async def _mine(self, owner_subject: str, request_id: str) -> uuid.UUID:
        rid = parse_id(request_id, "request")
        request = await self.repo.get_request(rid)
        if request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such request", code="request_not_found")
        return rid

    async def grants(self, owner_subject: str, request_id: str) -> list[GrantRecord]:
        rid = await self._mine(owner_subject, request_id)
        return list(await self.repo.list_grants([rid]))

    async def ask_platform(
        self, owner_subject: str, request_id: str, body: PlatformIncubationIn
    ) -> GrantRecord:
        """「请平台孵化」：记一张孵化授权，名称转平台，等这一版创意正式受理。"""
        rid = await self._mine(owner_subject, request_id)
        now = self.now()
        async with self.repo.transaction():
            await self.repo.lock_request(rid)
            if live_incubation(list(await self.repo.list_grants([rid])), now):
                raise _conflict("Already with the platform", "already_platform")
            grant = GrantRecord(
                id=uuid.uuid4(),
                request_id=rid,
                kind="incubate",
                status="pending",
                budget_limit=body.budget_limit,
                granted_by=owner_subject,
                granted_at=now,
            )
            await self.repo.add_grant(grant)
            await self.repo.set_incubation_mode(rid, "platform")
            request = await self.repo.get_request(rid)
            assert request is not None
            found = await self.repo.get_acceptance(rid, request.latest_version)
            if found is not None and found.kind == "formal" and not found.accepted:
                raise _conflict(
                    "This version was rejected by the platform: revise your idea first",
                    "version_rejected",
                )
            if found is not None and found.kind == "formal" and found.accepted:
                # 这一版正式受理过（以前平台孵化过又收回来）：不用再受理，授权直接生效
                await self.repo.update_grant(
                    grant.id,
                    status="active",
                    decided_by=found.decided_by,
                    decided_at=now,
                    expires_at=expiry(now),
                )
            return await self._grant(grant.id)

    async def take_back(self, owner_subject: str, request_id: str) -> None:
        """「我自己来」：平台对这个名称的授权立即撤销，运营立即看不到。"""
        rid = await self._mine(owner_subject, request_id)
        now = self.now()
        async with self.repo.transaction():
            await self.repo.lock_request(rid)
            for g in await self.repo.list_grants([rid]):
                if g.kind == "incubate" and g.status in ("pending", "active"):
                    await self.repo.update_grant(
                        g.id, status="revoked", ended_by=owner_subject, ended_at=now
                    )
            for inc in await self.repo.list_incubations(request_id=rid):
                if inc.mode == "platform" and inc.status in ("queued", "running", "waiting"):
                    await self.repo.update_incubation(
                        inc.id,
                        status="canceled",
                        ended_at=now,
                        last_error="The author took back the request",
                    )
            for t in await self.repo.list_tests(request_ids=[rid]):
                if t.mode in ("platform", "rerun") and t.status in ("queued", "running", "waiting"):
                    await self.repo.update_test(
                        t.id,
                        status="platform_error",
                        finished_at=now,
                        last_error="The author took back the request",
                    )
            # 运营没被接受的提议作废（decisions 10-29）
            for p in await self.repo.list_proposals(rid):
                if p.state == "pending":
                    await self.repo.update_proposal(p.id, state="void", decided_at=now)
            await self._to_self(rid, now)

    async def renew(self, owner_subject: str, request_id: str) -> GrantRecord:
        """续期：生效了的孵化授权（含刚过期的）续到 30 天后。"""
        rid = await self._mine(owner_subject, request_id)
        now = self.now()
        async with self.repo.transaction():
            await self.repo.lock_request(rid)
            request = await self.repo.get_request(rid)
            assert request is not None
            # 库里还记着 active 的（没过期的、过了期还没续的都算）
            found = next(
                (
                    g
                    for g in reversed(await self.repo.list_grants([rid]))
                    if g.kind == "incubate" and g.status == "active"
                ),
                None,
            )
            if found is None or request.incubation_mode != "platform":
                raise _conflict("Nothing to renew", "grant_not_renewable")
            await self.repo.update_grant(found.id, expires_at=expiry(now))
            return await self._grant(found.id)

    async def ask_platform_test(
        self, owner_subject: str, draft_id: str, body: PlatformTestIn
    ) -> GrantRecord:
        """「交平台测」：自助孵化的准专家交平台测这一次，等轻受理。"""
        draft = await self.repo.get_draft(parse_id(draft_id))
        request = (
            None if draft is None else await self.repo.get_request(draft.request_id)
        )
        if draft is None or request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such draft", code="draft_not_found")
        if draft.mode != "self":
            raise _conflict(
                "Platform drafts are tested by the platform already", "draft_not_self"
            )
        if draft.status != "pending_test":
            raise _conflict("This draft cannot be tested now", "draft_not_testable")
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            grant = GrantRecord(
                id=uuid.uuid4(),
                request_id=request.id,
                kind="test",
                draft_id=draft.id,
                status="pending",
                budget_limit=body.budget_limit,
                granted_by=owner_subject,
                granted_at=self.now(),
            )
            await self.repo.add_grant(grant)
            return grant

    async def withdraw_test(self, owner_subject: str, grant_id: str) -> None:
        """还没开测的平台测试，作者收回：授权撤销。"""
        grant = await self.repo.get_grant(parse_id(grant_id, "grant"))
        request = (
            None if grant is None else await self.repo.get_request(grant.request_id)
        )
        if (
            grant is None
            or request is None
            or request.owner_subject != owner_subject
            or grant.kind != "test"
        ):
            raise NotFoundError("No such grant", code="grant_not_found")
        async with self.repo.transaction():
            locked = await self.repo.get_grant(grant.id, for_update=True)
            assert locked is not None
            if locked.status not in ("pending", "active"):
                raise _conflict("This grant has ended", "grant_ended")
            tests = await self.repo.list_tests(draft_id=locked.draft_id)
            if any(
                t.mode == "platform" for t in tests if t.started_at >= locked.granted_at
            ):
                raise _conflict("The platform test has started", "test_started")
            await self.repo.update_grant(
                locked.id, status="revoked", ended_by=owner_subject, ended_at=self.now()
            )

    # ---------- 平台 ----------
    async def hand_back(self, request_id: str, operator: str) -> None:
        """平台把草稿交还作者：孵化授权结束，名称转自助。

        SDD 0046「平台孵化 + 自助测试」。
        """
        rid = parse_id(request_id, "request")
        now = self.now()
        async with self.repo.transaction():
            await self.repo.lock_request(rid)
            found = live_incubation(list(await self.repo.list_grants([rid])), now)
            if found is None or found.status != "active":
                raise NotFoundError("No such request", code="request_not_found")
            await self.repo.update_grant(
                found.id, status="ended", ended_by=operator, ended_at=now
            )
            await self._to_self(rid, now)

    async def decide_test(
        self, grant_id: str, body: LightAcceptanceIn, operator: str
    ) -> GrantRecord:
        """轻受理：接（授权生效，进测试栏待发起）或不接（写理由）。"""
        gid = parse_id(grant_id, "grant")
        async with self.repo.transaction():
            grant = await self.repo.get_grant(gid, for_update=True)
            if grant is None or grant.kind != "test":
                raise NotFoundError("No such grant", code="grant_not_found")
            if grant.status != "pending":
                raise _conflict("Already decided", "already_decided")
            if not body.accepted and not body.reason.strip():
                raise _conflict("Say why it is declined", "reason_required")
            now = self.now()
            await self.repo.update_grant(
                gid,
                status="active" if body.accepted else "declined",
                decided_by=operator,
                decided_at=now,
                reason=body.reason.strip() or None,
                expires_at=expiry(now) if body.accepted else None,
            )
            return await self._grant(gid)

    # ---------- 共用 ----------
    async def _to_self(self, rid: uuid.UUID, now: datetime) -> None:
        await self.repo.set_incubation_mode(rid, "self")
        request = await self.repo.get_request(rid)
        assert request is not None
        if await self.repo.get_acceptance(rid, request.latest_version) is None:
            await self.repo.add_acceptance(
                self_acceptance(rid, request.latest_version, now)
            )

    async def _grant(self, grant_id: uuid.UUID) -> GrantRecord:
        found = await self.repo.get_grant(grant_id)
        assert found is not None
        return found
