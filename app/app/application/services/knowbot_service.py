"""分身的创建与测试（SDD 0027 §一、§四）。

用户在网页端提交想法、运行方、验收用例；管理端建草稿（过渡做法：管理员上传清单，等「后端
自动生成」定了再接）、核规则出处、发起测试、刷新结果；测试通过即交付 knowmart，交付失败
可重试；「测试中」超时可退回待测试。只有测试通过的才交付。

发起测试先把草稿标成测试中并提交，再调运行方；调用失败退回待测试。运行方按 run_id 幂等，
knowmart 按交付单号幂等，所以重试都安全。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
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
    KnowmartClient,
    RequestRecord,
    RuntimeTestClient,
)
from app.domain.knowbots.datasets import binding_problems
from app.domain.knowbots.drafts import (
    KnowbotRequestIn,
    can_move,
    draft_manifest_problems,
)
from app.domain.knowbots.test_run import CONTRACT, RunRequestV1, RunResultV1

OWNER = re.compile(r"^[A-Za-z0-9._:@-]{1,512}$")
STUCK_GRACE = timedelta(minutes=5)


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


def _parse_id(value: str | uuid.UUID, kind: str = "draft") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"No such {kind}", code=f"{kind}_not_found") from exc


class KnowbotCreationService:
    def __init__(
        self,
        repo: KnowlabRepository,
        runtimes: RuntimeTestClient,
        knowmart: KnowmartClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.runtimes = runtimes
        self.knowmart = knowmart
        self.now = now

    # ---------- 用户：提交与查看 ----------
    async def submit(self, owner_subject: str, body: KnowbotRequestIn) -> RequestRecord:
        if not OWNER.fullmatch(owner_subject or ""):
            raise BadRequestError("The owner is invalid", code="owner_invalid")
        if body.runtime not in self.runtimes.registered():
            raise BadRequestError(
                "This runtime is not registered", code="runtime_not_registered"
            )
        async with self.repo.transaction():
            return await self.repo.create_request(
                owner_subject=owner_subject,
                title=body.title,
                runtime=body.runtime,
                idea=body.idea.model_dump(mode="json", exclude_none=True),
                cases=[c.model_dump(mode="json") for c in body.cases],
            )

    async def my_requests(
        self, owner_subject: str
    ) -> list[tuple[RequestRecord, Sequence[DraftRecord]]]:
        if not OWNER.fullmatch(owner_subject or ""):
            return []
        out = []
        for request in await self.repo.list_requests_for_owner(owner_subject):
            out.append((request, await self.repo.list_drafts(request_id=request.id)))
        return out

    # ---------- 管理端：提交与草稿 ----------
    async def all_requests(
        self,
    ) -> list[tuple[RequestRecord, Sequence[DraftRecord]]]:
        out = []
        for request in await self.repo.list_requests():
            out.append((request, await self.repo.list_drafts(request_id=request.id)))
        return out

    async def request_detail(self, request_id: str) -> RequestRecord:
        request = await self.repo.get_request(_parse_id(request_id, "request"))
        if request is None:
            raise NotFoundError("No such request", code="request_not_found")
        return request

    async def add_draft(self, request_id: str, manifest: object) -> DraftRecord:
        """过渡做法：管理员上传清单建草稿。版本号 = 已交付次数 + 1，修订号递增。"""
        rid = _parse_id(request_id, "request")
        async with self.repo.transaction():
            request = await self.repo.get_request(rid)
            if request is None:
                raise NotFoundError("No such request", code="request_not_found")
            await self.repo.lock_request(rid)
            drafts = await self.repo.list_drafts(request_id=rid)
            if any(d.status in {"pending_test", "testing", "passed"} for d in drafts):
                raise _conflict(
                    "Finish the draft in progress first", "draft_in_progress"
                )
            version = str(sum(d.status == "delivered" for d in drafts) + 1)
            revision = max((d.revision for d in drafts), default=0) + 1
            problems = draft_manifest_problems(
                manifest, runtime=request.runtime, version=version
            )
            if not problems:
                # 分身运行时只读这一版绑定的、这个用户自己的数据（C-L2）
                assert isinstance(manifest, dict)
                owned = {
                    str(d.id): (d.version, d.sha256)
                    for d in await self.repo.list_datasets(request.owner_subject)
                }
                problems = binding_problems(manifest.get("data_bindings"), owned)
            if problems:
                raise BadRequestError("; ".join(problems), code="manifest_invalid")
            assert isinstance(manifest, dict)
            return await self.repo.create_draft(
                request_id=rid, revision=revision, version=version, manifest=manifest
            )

    async def drafts(
        self, status: str | None = None
    ) -> list[tuple[DraftRecord, RequestRecord]]:
        out = []
        for draft in await self.repo.list_drafts(status=status):
            request = await self.repo.get_request(draft.request_id)
            if request is not None:
                out.append((draft, request))
        return out

    async def _locked(self, draft_id: str | uuid.UUID) -> DraftRecord:
        draft = await self.repo.get_draft(_parse_id(draft_id), for_update=True)
        if draft is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        return draft

    async def _move(self, draft: DraftRecord, target: str, **changes: Any) -> None:
        if not can_move(draft.status, target):
            raise _conflict(
                f"A {draft.status} draft cannot become {target}", "draft_state"
            )
        await self.repo.update_draft(draft.id, status=target, **changes)

    async def review_rules(self, draft_id: str, reviewer: str) -> None:
        """发起测试前逐条核过规则出处（C-L1）。只在待测试时做。"""
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status != "pending_test":
                raise _conflict("Only a pending draft is reviewed", "draft_state")
            await self.repo.update_draft(
                draft.id, rules_reviewed_by=reviewer, rules_reviewed_at=self.now()
            )

    # ---------- 管理端：测试 ----------
    async def start_test(
        self, draft_id: str, *, budget_limit: str, timeout_seconds: int
    ) -> RunResultV1:
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status == "pending_test" and not draft.rules_reviewed_by:
                raise _conflict(
                    "Review every rule source before testing", "rules_not_reviewed"
                )
            request = await self.repo.get_request(draft.request_id)
            assert request is not None
            attempt = draft.run_attempts + 1
            run = RunRequestV1.model_validate(
                {
                    "contract": CONTRACT,
                    "run_id": f"knowlab-{draft.id}-{attempt}",
                    "owner_subject": request.owner_subject,
                    "manifest": draft.manifest,
                    "cases": request.cases,
                    "budget": {"limit": budget_limit},
                    "timeout_seconds": timeout_seconds,
                }
            )
            await self._move(
                draft,
                "testing",
                run_id=run.run_id,
                run_attempts=attempt,
                run_started_at=self.now(),
                run_timeout_seconds=timeout_seconds,
                report=None,
                last_error=None,
            )
        try:
            result = await self.runtimes.start(request.runtime, run)
        except AppException as exc:
            async with self.repo.transaction():
                current = await self._locked(draft.id)
                if current.status == "testing" and current.run_id == run.run_id:
                    await self._move(
                        current, "pending_test", last_error=f"start failed: {exc.code}"
                    )
            raise
        if result.final:
            await self._finish(draft.id, result)
        return result

    async def refresh(self, draft_id: str) -> DraftRecord:
        """问运行方结果；有了结论就记下，通过的接着交付（交付失败只记错误，可重试）。"""
        draft = await self.repo.get_draft(_parse_id(draft_id))
        if draft is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        if draft.status == "testing" and draft.run_id:
            request = await self.repo.get_request(draft.request_id)
            assert request is not None
            result = await self.runtimes.status(request.runtime, draft.run_id)
            if result.run_id != draft.run_id:
                raise _conflict("The runtime answered another run", "run_mismatch")
            if result.final:
                await self._finish(draft.id, result)
        draft = await self.repo.get_draft(draft.id)
        assert draft is not None
        if draft.status == "passed":
            try:
                await self.deliver(str(draft.id))
            except AppException:
                pass  # 错误已记在草稿上，管理端可以重试交付
            draft = await self.repo.get_draft(draft.id)
            assert draft is not None
        return draft

    async def _finish(self, draft_id: uuid.UUID, result: RunResultV1) -> None:
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status != "testing" or draft.run_id != result.run_id:
                return  # 已经退回或换了一次测试：旧结果不算数
            await self._move(
                draft,
                "passed" if result.status == "passed" else "failed",
                report=result.model_dump(mode="json"),
            )

    async def deliver(self, draft_id: str) -> DraftRecord:
        draft = await self.repo.get_draft(_parse_id(draft_id))
        if draft is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        if draft.status != "passed":
            raise _conflict("Only a passed draft is delivered", "draft_state")
        request = await self.repo.get_request(draft.request_id)
        assert request is not None
        try:
            delivered = await self.knowmart.deliver(
                delivery_id=f"knowlab-draft-{draft.id}",
                owner_subject=request.owner_subject,
                knowbot_ref=f"knowlab-{request.id}",
                manifest=draft.manifest,
            )
        except AppException as exc:
            async with self.repo.transaction():
                await self.repo.update_draft(
                    draft.id, last_error=f"delivery failed: {exc.code}"
                )
            raise
        async with self.repo.transaction():
            current = await self._locked(draft.id)
            if current.status == "passed":
                await self._move(
                    current, "delivered", delivery=delivered, last_error=None
                )
        found = await self.repo.get_draft(draft.id)
        assert found is not None
        return found

    async def reset_stuck(self, draft_id: str) -> None:
        """「测试中」超过超时（再宽限 5 分钟）还没结论，退回待测试；规则核对保留。"""
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status != "testing" or draft.run_started_at is None:
                raise _conflict("Only a testing draft is reset", "draft_state")
            deadline = (
                draft.run_started_at
                + timedelta(seconds=draft.run_timeout_seconds or 0)
                + STUCK_GRACE
            )
            if self.now() < deadline:
                raise _conflict("The test is still within its time", "not_stuck")
            await self._move(draft, "pending_test", last_error="timed out; returned")
