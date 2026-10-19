"""分身的创建与测试（SDD 0027 §一、§四）。

用户在网页端提交想法、运行方、验收用例，之后可以给想法写补充方法（SDD 0035）；
管理端建草稿（过渡做法：管理员上传清单，等「后端
自动生成」定了再接）、核规则出处、发起测试、刷新结果；测试通过即交付 knowmart，交付失败
可重试；「测试中」超时可退回待测试。只有测试通过的才交付。

发起测试先把草稿标成测试中并提交，再调运行方；调用失败退回待测试。运行方按 run_id 幂等，
knowmart 按交付单号幂等，所以重试都安全。

刷新时，跑完了的用例各取一次产出——成稿与各步交回物（SDD 0037）：运营看来验证这一稿，用户
只看自己的。取不到的下次刷新再取；运行方说没有的记一句原因，不再取。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import (
    AmendmentRecord,
    DraftRecord,
    KnowlabRepository,
    KnowmartClient,
    RequestRecord,
    RuntimeTestClient,
    TestOutputRecord,
)
from app.domain.knowbots.datasets import binding_problems
from app.domain.knowbots.drafts import (
    CONSENT_VERSION,
    MAX_AMENDMENT_CHARS,
    MAX_AMENDMENTS,
    MAX_DECLINE_CHARS,
    OUTPUTS_KEEP_DAYS,
    KnowbotRequestIn,
    amendment_ref_problems,
    amendment_refs,
    amendment_status,
    amendments_to_handle,
    can_move,
    draft_manifest_problems,
)
from app.domain.knowbots.test_run import (
    CONTRACT,
    RunRequestV1,
    RunResultV1,
    first_route,
)

log = logging.getLogger(__name__)

OWNER = re.compile(r"^[A-Za-z0-9._:@-]{1,512}$")
NO_OUTPUTS = "这次测试没有取产出：运行方没有这条用例的成稿与交回物"
OUTPUTS_PER_REFRESH = 10  # 一次刷新最多取几条用例的产出，其余下次刷新再取
STUCK_GRACE = timedelta(minutes=5)
# 一个问题最多等多久（所有者定 3 天）；过了运行方把那条用例记 needs_human
MAX_WAIT_SECONDS = 3 * 24 * 3600


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
        on_refresh: Callable[[DraftRecord, RequestRecord], Awaitable[None]]
        | None = None,
    ) -> None:
        self.repo = repo
        self.runtimes = runtimes
        self.knowmart = knowmart
        self.now = now
        self.on_refresh = on_refresh

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
                consent_version=CONSENT_VERSION,
            )

    async def consent(self, owner_subject: str, request_id: str) -> None:
        """用户同意新一版测试数据说明（SDD 0037）：之后的测试才取、才显示产出。"""
        request, _ = await self.my_request(owner_subject, request_id)
        if request.consent_version >= CONSENT_VERSION:
            return
        async with self.repo.transaction():
            await self.repo.set_consent_version(request.id, CONSENT_VERSION)

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

    async def my_request(
        self, owner_subject: str, request_id: str
    ) -> tuple[RequestRecord, Sequence[DraftRecord]]:
        """自己的一个创意（SDD 0037 web端）。不是自己的与没有的一样，都是 404。"""
        request = await self.repo.get_request(_parse_id(request_id, "request"))
        if request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such request", code="request_not_found")
        return request, await self.repo.list_drafts(request_id=request.id)

    async def my_draft(
        self, owner_subject: str, draft_id: str
    ) -> tuple[DraftRecord, RequestRecord]:
        """自己的一个分身（一稿）。不是自己的与没有的一样，都是 404。"""
        draft = await self.repo.get_draft(_parse_id(draft_id))
        request = (
            None if draft is None else await self.repo.get_request(draft.request_id)
        )
        if draft is None or request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such draft", code="draft_not_found")
        return draft, request

    async def my_knowbots(
        self, owner_subject: str
    ) -> list[tuple[DraftRecord, RequestRecord]]:
        """做出来的专家：交付了的各稿，新的在前。"""
        out = [
            (d, r)
            for r, drafts in await self.my_requests(owner_subject)
            for d in drafts
            if d.status == "delivered"
        ]
        return sorted(
            out, key=lambda pair: pair[0].created_at or self.now(), reverse=True
        )

    async def draft(self, draft_id: str) -> DraftRecord:
        found = await self.repo.get_draft(_parse_id(draft_id))
        if found is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        return found

    async def test_outputs(
        self, draft: DraftRecord, request: RequestRecord
    ) -> Sequence[TestOutputRecord] | None:
        """这一稿最近一次测试取到的产出（SDD 0037）。

        创意没同意新说明的回 None（不显示）。
        """
        if request.consent_version < CONSENT_VERSION:
            return None
        if not draft.run_id:
            return []
        return await self.repo.list_test_outputs(draft.id, draft.run_id)

    async def draft_with_request(
        self, draft_id: str
    ) -> tuple[DraftRecord, RequestRecord]:
        found = await self.draft(draft_id)
        request = await self.repo.get_request(found.request_id)
        assert request is not None
        return found, request

    async def request_detail(self, request_id: str) -> RequestRecord:
        request = await self.repo.get_request(_parse_id(request_id, "request"))
        if request is None:
            raise NotFoundError("No such request", code="request_not_found")
        return request

    async def request_drafts(self, request_id: uuid.UUID) -> Sequence[DraftRecord]:
        return await self.repo.list_drafts(request_id=request_id)

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
            if not problems:
                # 出处引的补充必须是这个想法的、没被「不照做」（SDD 0035 §二.3）
                problems = amendment_ref_problems(
                    manifest,
                    {
                        str(a.id): a.declined_at is not None
                        for a in await self.repo.list_amendments(rid)
                    },
                )
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
            blocking, _newer = await self.amendments_to_handle(draft)
            if blocking:
                # 建稿前用户写的补充，这一稿要么用进出处、要么写理由不照做
                # （SDD 0035 §二.5）
                raise _conflict(
                    "Handle these amendments first (cite them in this draft's rule "
                    "sources, or decline them with a reason): " + ", ".join(blocking),
                    "amendments_open",
                )
            await self.repo.update_draft(
                draft.id, rules_reviewed_by=reviewer, rules_reviewed_at=self.now()
            )

    async def amendments_to_handle(self, draft: DraftRecord) -> tuple[list[str], int]:
        """（这一稿核对出处前要处理的补充，这一稿建好之后才提交的条数）。"""
        found = await self.repo.list_amendments(draft.request_id)
        return amendments_to_handle(
            draft.manifest,
            draft.created_at,
            [(str(a.id), a.created_at, a.declined_at is not None) for a in found],
        )

    # ---------- 补充方法（SDD 0035） ----------
    async def amend(
        self, owner_subject: str, request_id: str, text: str
    ) -> AmendmentRecord:
        """用户给自己的想法写一条补充。原话写下后不改、不删；别人的想法当没有。"""
        text = (text or "").strip()
        if not text or len(text) > MAX_AMENDMENT_CHARS:
            raise BadRequestError(
                f"Write 1 to {MAX_AMENDMENT_CHARS} characters", code="amendment_invalid"
            )
        rid = _parse_id(request_id, "request")
        async with self.repo.transaction():
            request = await self.repo.get_request(rid)
            if request is None or request.owner_subject != owner_subject:
                raise NotFoundError("No such request", code="request_not_found")
            await self.repo.lock_request(rid)
            if len(await self.repo.list_amendments(rid)) >= MAX_AMENDMENTS:
                raise _conflict(
                    f"At most {MAX_AMENDMENTS} amendments", "too_many_amendments"
                )
            return await self.repo.create_amendment(
                request_id=rid, owner_subject=owner_subject, text=text
            )

    async def amendments(
        self, request_id: uuid.UUID, drafts: Sequence[DraftRecord]
    ) -> list[tuple[AmendmentRecord, str, int | None]]:
        """每条补充和它的状态。

        （补充，open / used / declined，用进的最新一稿的修订号）。
        """
        pairs = [(d.revision, d.manifest) for d in drafts]
        return [
            (a, *amendment_status(str(a.id), a.declined_at is not None, pairs))
            for a in await self.repo.list_amendments(request_id)
        ]

    async def decline_amendment(
        self, amendment_id: str, reason: str, decliner: str
    ) -> None:
        """照不了的补充写理由「不照做」（用户看得到）。最新一稿还引着它的不能不照做；更早的稿
        引过、用户又写了新补充撤回它的，可以（审读 10-19 第 7 条）。"""
        reason = (reason or "").strip()
        if not reason or len(reason) > MAX_DECLINE_CHARS:
            raise BadRequestError(
                "Say why this amendment is not followed", code="reason_required"
            )
        aid = _parse_id(amendment_id, "amendment")
        async with self.repo.transaction():
            found = await self.repo.get_amendment(aid)
            if found is None:
                raise NotFoundError("No such amendment", code="amendment_not_found")
            # 和建草稿串行：不照做与被新一稿引用不会同时成立
            await self.repo.lock_request(found.request_id)
            amendment = await self.repo.get_amendment(aid, for_update=True)
            assert amendment is not None
            if amendment.declined_at is not None:
                raise _conflict("Already declined", "amendment_declined")
            drafts = await self.repo.list_drafts(request_id=amendment.request_id)
            latest = max(
                drafts, key=lambda d: (int(d.version), d.revision), default=None
            )
            if latest is not None and str(aid) in amendment_refs(latest.manifest)[0]:
                raise _conflict("The latest draft cites it", "amendment_used")
            await self.repo.decline_amendment(
                aid, reason=reason, by=decliner, at=self.now()
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
                    "max_wait_seconds": MAX_WAIT_SECONDS,
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
            if self.on_refresh is not None:
                await self.on_refresh(draft, request)  # 先补送答过但没送到的答案
            result = await self.runtimes.status(request.runtime, draft.run_id)
            if result.run_id != draft.run_id:
                raise _conflict("The runtime answered another run", "run_mismatch")
            if result.final:
                await self._finish(draft.id, result)
            else:
                await self._progress(draft.id, result)
            await self._collect_outputs(draft, request, result)
        elif draft.run_id and draft.report:
            # 测完了还缺的（上次刷新时运行方没答上）：补取
            request = await self.repo.get_request(draft.request_id)
            assert request is not None
            await self._collect_outputs(
                draft, request, RunResultV1.model_validate(draft.report)
            )
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

    async def _collect_outputs(
        self, draft: DraftRecord, request: RequestRecord, result: RunResultV1
    ) -> None:
        """有结论的用例各取一次产出（SDD 0037），一次刷新最多取 OUTPUTS_PER_REFRESH 条。

        只为同意了新说明的创意取。运行方不可达、还是旧版：这一轮就停，下次刷新再
        取；回包不合契约、答错用例：确定的错，记下原因不再取；运行方说没有：记一句
        原因（审读 10-19）。
        """
        if not draft.run_id or result.run_id != draft.run_id:
            return
        if request.consent_version < CONSENT_VERSION:
            return
        have = await self.repo.test_output_cases(draft.id, draft.run_id)
        todo = [c.case_id for c in result.cases if c.case_id not in have]
        for case_id in todo[:OUTPUTS_PER_REFRESH]:
            note = None
            try:
                found = await self.runtimes.outputs(
                    request.runtime, draft.run_id, case_id
                )
            except ServiceUnavailableError as exc:
                log.warning(
                    "knowbot test outputs not fetched draft=%s case=%s code=%s",
                    draft.id,
                    case_id,
                    exc.code,
                )
                return
            except AppException as exc:
                found, note = None, f"运行方回的产出不合约定（{exc.code}），没有存"
            async with self.repo.transaction():
                await self.repo.put_test_output(
                    TestOutputRecord(
                        draft_id=draft.id,
                        run_id=draft.run_id,
                        case_id=case_id,
                        outputs=None
                        if found is None
                        else found.model_dump(mode="json"),
                        note=note or (NO_OUTPUTS if found is None else None),
                    )
                )

    async def expire_test_outputs(self) -> int:
        """交付或被下一个分身取代满 90 天的分身：清掉产出正文（定时任务每小时一次）。"""
        async with self.repo.transaction():
            return await self.repo.expire_test_outputs(
                self.now() - timedelta(days=OUTPUTS_KEEP_DAYS)
            )

    async def _progress(self, draft_id: uuid.UUID, result: RunResultV1) -> None:
        """还没结论：记下进度；在等人回答的，把问题记进待回答（有人值守）。"""
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status != "testing" or draft.run_id != result.run_id:
                return
            await self.repo.update_draft(
                draft.id, report=result.model_dump(mode="json")
            )
            if result.questions:
                await self.repo.add_questions(
                    draft.id,
                    result.run_id,
                    [
                        q.model_dump(mode="json") | {"route": first_route(q.kind)}
                        for q in result.questions
                    ],
                )

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
                    current,
                    "delivered",
                    delivery={**delivered, "delivered_at": self.now().isoformat()},
                    last_error=None,
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
            # 等人回答的时间不计入超时：加上已经等的；正在等的，再给足最长等待
            report = draft.report or {}
            waited = int(report.get("waited_seconds") or 0)
            if report.get("status") == "waiting":
                waited += MAX_WAIT_SECONDS
            deadline = (
                draft.run_started_at
                + timedelta(seconds=(draft.run_timeout_seconds or 0) + waited)
                + STUCK_GRACE
            )
            if self.now() < deadline:
                raise _conflict("The test is still within its time", "not_stuck")
            await self._move(draft, "pending_test", last_error="timed out; returned")
