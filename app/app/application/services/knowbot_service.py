"""创意、受理、孵化、测试（SDD 0027、0037 第四节）。

专家的两道门与上架在 knowbot_experts。

没有退回：每个过程跑一次出一个结果，结果定了不改。

- 申请与修改（web端，用户）：申请出创意 v1，改一次出一个新版本；名称、运行方一生不变。
- 受理（管理端）：给某一版写受理单，查三样；不受理写理由，用户改创意出新版本再受理。
- 孵化（管理端，过渡做法：贴清单）：只能选受理了的版本；规则出处只认创意版本里的句子
  （补充方法 0035 取消，旧的留作历史、只读）。可以请用户审核：等用户答的不能测，用户说
  不行的不再测。
- 测试（管理端发起的平台测试，或作者自助）：都在作者的账号下、他的电脑上、用他的模型 key
  跑（decisions ⑯）；每次一个新编号；同一个名称同时只有一个在跑。平台出错
  （运行方报错、超时、开跑失败、运营判定卡住）结束这一次，准专家回到待测，再测是新
  编号。通过即出专家，版本号就是准专家的 x；x 已经出过专家的准专家不许再测（再孵化）。

发起测试先建测试、把准专家标成测试中并提交，再调运行方；运行方按 run_id 幂等，重试安全。
页面轮询时刷新，后台每分钟也刷一次在跑的（作者不开页面也更新，tasks/knowbot_refresh）。
刷新时，跑完了的用例各取一次产出——成稿与各步交回物（SDD 0037）：运营看来验证这一稿，用户
只看自己的。取不到的下次刷新再取；运行方说没有的记一句原因，不再取。
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import (
    AcceptanceRecord,
    AmendmentRecord,
    DraftRecord,
    ExpertRecord,
    GrantRecord,
    KnowlabRepository,
    RequestRecord,
    RequestVersionRecord,
    RuntimeTestClient,
    TestOutputRecord,
    TestRecord,
)
from app.application.services.knowbot_grants import (
    live_incubation,
    self_acceptance,
)
from app.domain.knowbots.drafts import (
    CONSENT_VERSION,
    OUTPUTS_KEEP_DAYS,
    KnowbotRequestIn,
    RuntimeIncubationIn,
    amendment_status,
    can_move,
    case_question_digest,
)
from app.domain.knowbots.lifecycle import (
    ACTIVE_TESTS,
    AcceptanceIn,
    RevisionIn,
    UserReviewIn,
    review_state,
    status_from_run,
)
from app.domain.knowbots.platform_grants import expiry as grant_expiry
from app.domain.knowbots.test_run import (
    CONTRACT,
    RunCase,
    RunRequestV1,
    RunResultV1,
    first_route,
)

if TYPE_CHECKING:
    from app.application.services.knowbot_test_data import KnowbotTestData

log = logging.getLogger(__name__)

OWNER = re.compile(r"^[A-Za-z0-9._:@-]{1,512}$")
NO_OUTPUTS = "这次测试没有取产出：运行方没有这条用例的成稿与交回物"
OUTPUTS_PER_REFRESH = 10  # 一次刷新最多取几条用例的产出，其余下次刷新再取
STUCK_GRACE = timedelta(minutes=5)
# 测试运行授权的时限：测试的超时加最长等人，再留一小时；最长 14 天
GRANT_MARGIN_SECONDS = 3600
GRANT_MAX_SECONDS = 14 * 24 * 3600
# 一个问题最多等多久（所有者定 3 天）；过了运行方把那条用例记 needs_human
MAX_WAIT_SECONDS = 3 * 24 * 3600
MAX_VERSIONS = 200  # 一个创意最多改这么多版
REFRESH_BATCH = 200  # 后台一次最多刷新几个测试（多的下一次再刷）


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


async def require_model_key(
    runtimes: RuntimeTestClient, request: RequestRecord
) -> None:
    """作者在 knowbench 还没填模型 key：发起前就拦（SDD 0046 第三节「模型 key 用他在
    knowbench 填的，没填不能发起」）。测试与孵化一轮都在作者的账号下、用他的 key 跑
    （decisions ⑯），平台发起的也一样拦。运行方说不准（还没有这个接口）不拦，跑起来
    照旧会停。"""
    if await runtimes.model_key(request.runtime, request.owner_subject) is False:
        raise _conflict(
            "The author has no model key in knowbench yet", "model_key_missing"
        )


def parse_id(value: str | uuid.UUID, kind: str = "draft") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"No such {kind}", code=f"{kind}_not_found") from exc


def cases_for_test(
    cases: list[dict[str, Any]], answers: dict[str, Any]
) -> list[dict[str, Any]]:
    """这一版的用例并上用户答过的开步前提问（用例自己写了的不覆盖）。

    答案记着当时那条用例问的是哪一句（摘要）：编号对上、问的也还是同一句才并，不然这一问
    照样问用户（Fable 评审 P1-6）。
    """
    merged = copy.deepcopy(cases)
    for case in merged:
        given = answers.get(str(case.get("case_id")))
        if not isinstance(given, dict) or given.get("question") != case_question_digest(
            case
        ):
            continue
        preset = case.setdefault("input", {}).setdefault("answers", {})
        for key, value in (given.get("answers") or {}).items():
            preset.setdefault(key, value)
    return merged


class KnowbotCreationService:
    def __init__(
        self,
        repo: KnowlabRepository,
        runtimes: RuntimeTestClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_refresh: Callable[[TestRecord, RequestRecord], Awaitable[None]]
        | None = None,
        test_data: KnowbotTestData | None = None,
    ) -> None:
        self.repo = repo
        self.runtimes = runtimes
        self.now = now
        self.on_refresh = on_refresh
        # 配了 knowbase：测试集与测试里专家提出的数据（SDD 0046 第三节）
        self.test_data = test_data

    # ---------- 用户：申请与修改 ----------
    async def submit(self, owner_subject: str, body: KnowbotRequestIn) -> RequestRecord:
        """申请：出创意 v1。"""
        if not OWNER.fullmatch(owner_subject or ""):
            raise BadRequestError("The owner is invalid", code="owner_invalid")
        if body.runtime not in self.runtimes.registered():
            raise BadRequestError(
                "This runtime is not registered", code="runtime_not_registered"
            )
        async with self.repo.transaction():
            created = await self.repo.create_request(
                owner_subject=owner_subject,
                title=body.title,
                runtime=body.runtime,
                idea=body.idea.model_dump(mode="json", exclude_none=True),
                cases=[c.model_dump(mode="json") for c in body.cases],
                consent_version=CONSENT_VERSION,
            )
            # 选了模式（SDD 0046 第四节）：自助的记「自助（不受理）」直接进工作稿；
            # 请平台的同时记一张孵化授权，等运营正式受理
            if body.mode == "self":
                await self.repo.add_acceptance(
                    self_acceptance(created.id, 1, self.now())
                )
            else:
                assert body.budget_limit is not None
                await self.repo.add_grant(
                    GrantRecord(
                        id=uuid.uuid4(),
                        request_id=created.id,
                        kind="incubate",
                        status="pending",
                        budget_limit=body.budget_limit,
                        granted_by=owner_subject,
                        granted_at=self.now(),
                    )
                )
                await self.repo.set_incubation_mode(created.id, "platform")
            found = await self.repo.get_request(created.id)
            assert found is not None
            return found

    async def submit_from_runtime(
        self,
        owner_subject: str,
        body: RuntimeIncubationIn,
        *,
        runtime: str,
    ) -> tuple[RequestRecord, bool]:
        """knowbench 替用户提的孵化请求（请专家方案设计第 4.9 节第 6 条、
        专家孵化方案第十三节）：
        以这个用户的名义建一份种类为「问题」的申请，种子就是那句问题；模式自己来（只有他看得到、
        建了不花钱），记来源，附他的模型直接答过的回答作参考（只算提议）。先不带用例、未同意测试
        数据说明，他补用例、点同意才能测。同一个幂等钥匙只建一份：回（申请，是不是这次新建的）。"""
        if not OWNER.fullmatch(owner_subject or ""):
            raise BadRequestError("The owner is invalid", code="owner_invalid")
        for mine in await self.repo.list_requests_for_owner(owner_subject):
            if (mine.origin or {}).get("key") == body.key:
                return mine, False
        question = body.question.strip()
        title = question.splitlines()[0].strip()[:80] or question[:80]
        async with self.repo.transaction():
            created = await self.repo.create_request(
                owner_subject=owner_subject,
                title=title,
                runtime=runtime,
                idea={"kind": "question", "question": question},
                cases=[],
                consent_version=0,
                origin={
                    "app": runtime,
                    "project_id": body.project_id,
                    "task_id": body.task_id,
                    "key": body.key,
                },
                reference_answer=(body.reference_answer or "").strip() or None,
            )
            await self.repo.add_acceptance(self_acceptance(created.id, 1, self.now()))
        found = await self.repo.get_request(created.id)
        assert found is not None
        return found, True

    async def default_mode(self, owner_subject: str) -> str:
        """新申请默认的模式：他上次选的。

        第一次没有默认，让他自己选（SDD 0046 第四节）。
        """
        mine = await self.repo.list_requests_for_owner(owner_subject)
        latest = max(mine, key=lambda r: r.created_at or self.now(), default=None)
        if latest is None:
            return ""
        grants = await self.repo.list_grants([latest.id])
        asked = any(g.kind == "incubate" for g in grants)
        return "platform" if asked else "self"

    async def revise(
        self, owner_subject: str, request_id: str, body: RevisionIn
    ) -> RequestVersionRecord:
        """改创意：出一个新版本，旧版本不变；正在跑的测试不受影响。"""
        request = await self.my_request(owner_subject, request_id)
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            current = await self.repo.get_request(request.id)
            assert current is not None
            if current.latest_version >= MAX_VERSIONS:
                raise _conflict(f"At most {MAX_VERSIONS} versions", "too_many_versions")
            added = await self.repo.add_version(
                request_id=request.id,
                version=current.latest_version + 1,
                idea=body.idea.model_dump(mode="json", exclude_none=True),
                cases=[c.model_dump(mode="json") for c in body.cases],
                consent_version=CONSENT_VERSION,
                created_by=owner_subject,
            )
            # 自助的新版本照样「自助（不受理）」；平台的新版本等运营正式受理
            if current.incubation_mode == "self":
                await self.repo.add_acceptance(
                    self_acceptance(request.id, added.version, self.now())
                )
            return added

    async def consent(self, owner_subject: str, request_id: str) -> None:
        """用户同意新一版测试数据说明（SDD 0037）：之后的测试才取、才显示产出。"""
        request = await self.my_request(owner_subject, request_id)
        if request.consent_version >= CONSENT_VERSION:
            return
        async with self.repo.transaction():
            await self.repo.set_consent_version(request.id, CONSENT_VERSION)

    async def my_request(self, owner_subject: str, request_id: str) -> RequestRecord:
        """自己的一个创意。不是自己的与没有的一样，都是 404。"""
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such request", code="request_not_found")
        return request

    async def my_draft(
        self, owner_subject: str, draft_id: str
    ) -> tuple[DraftRecord, RequestRecord]:
        """自己的一位准专家。不是自己的与没有的一样，都是 404。"""
        draft = await self.repo.get_draft(parse_id(draft_id))
        request = (
            None if draft is None else await self.repo.get_request(draft.request_id)
        )
        if draft is None or request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such draft", code="draft_not_found")
        return draft, request

    async def my_test(
        self, owner_subject: str, test_id: str
    ) -> tuple[TestRecord, RequestRecord]:
        """自己的一次测试。不是自己的与没有的一样，都是 404。"""
        test, request = await self.test_with_request(test_id)
        if request.owner_subject != owner_subject:
            raise NotFoundError("No such test", code="test_not_found")
        return test, request

    async def test_outputs(
        self, test: TestRecord, request: RequestRecord
    ) -> Sequence[TestOutputRecord] | None:
        """这次测试取到的产出（SDD 0037）。创意没同意新说明的回 None（不显示）。"""
        if request.consent_version < CONSENT_VERSION:
            return None
        return await self.repo.list_test_outputs(test.draft_id, test.run_id)

    # ---------- 管理端：查 ----------
    async def request_detail(self, request_id: str) -> RequestRecord:
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None:
            raise NotFoundError("No such request", code="request_not_found")
        return request

    async def draft(self, draft_id: str) -> DraftRecord:
        found = await self.repo.get_draft(parse_id(draft_id))
        if found is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        return found

    async def test_with_request(self, test_id: str) -> tuple[TestRecord, RequestRecord]:
        test = await self.repo.get_test(parse_id(test_id, "test"))
        if test is None:
            raise NotFoundError("No such test", code="test_not_found")
        request = await self.repo.get_request(test.request_id)
        assert request is not None
        return test, request

    async def operator_draft(self, draft_id: str) -> DraftRecord:
        """管理端按编号取准专家：运营只看得到作者授权给平台的（SDD 0046 第四节）——
        孵化授权还有效时的平台准专家，或作者交平台测的那一位；其余与没有的一样，
        都是 404。"""
        found = await self.draft(draft_id)
        if not await self._shown_to_operator(found):
            raise NotFoundError("No such draft", code="draft_not_found")
        return found

    async def operator_test(self, test_id: str) -> tuple[TestRecord, RequestRecord]:
        """管理端按编号取测试：平台测的、测的是运营看得到的准专家；其余与没有的一样，
        都是 404（SDD 0046 第四节）。"""
        test, request = await self.test_with_request(test_id)
        draft = await self.repo.get_draft(test.draft_id)
        if (
            test.mode != "platform"
            or draft is None
            or not await self._shown_to_operator(draft)
        ):
            raise NotFoundError("No such test", code="test_not_found")
        return test, request

    async def _shown_to_operator(self, draft: DraftRecord) -> bool:
        grants = await self.repo.list_grants([draft.request_id])
        if draft.mode == "platform" and live_incubation(grants, self.now()):
            return True
        return any(
            g.kind == "test" and g.draft_id == draft.id and g.status != "declined"
            for g in grants
        )

    async def platform_budget(self, draft: DraftRecord) -> str:
        """平台测试的费用上限：作者在授权里设的（decisions ⑯）。平台孵化的准专家用孵化
        授权的，自助孵化的用这一次测试授权的；授权不在（没生效、撤了、到期）不能发起。"""
        grants = await self.repo.list_grants([draft.request_id])
        now = self.now()
        if draft.mode == "platform":
            found = live_incubation(grants, now)
            if found is not None and found.status == "active":
                return f"{found.budget_limit:.2f}"
        else:
            found = next(
                (
                    g
                    for g in grants
                    if g.kind == "test"
                    and g.draft_id == draft.id
                    and g.status == "active"
                ),
                None,
            )
            if found is not None:
                return f"{found.budget_limit:.2f}"
        raise _conflict("The author has not authorised this test", "not_authorised")

    async def amendments(
        self, request_id: uuid.UUID, drafts: Sequence[DraftRecord]
    ) -> list[tuple[AmendmentRecord, str, int | None]]:
        """旧的补充方法（SDD 0035，已取消，只读）。

        （补充，open / used / declined，用进的最新一稿的修订号）。
        """
        pairs = [(d.revision, d.manifest) for d in drafts]
        return [
            (a, *amendment_status(str(a.id), a.declined_at is not None, pairs))
            for a in await self.repo.list_amendments(request_id)
        ]

    # ---------- 管理端：受理 ----------
    async def accept(
        self, request_id: str, version: int, body: AcceptanceIn, decider: str
    ) -> AcceptanceRecord:
        """给创意的某一版写正式受理单。一个版本一张，写了不改。

        只受理作者请了平台孵化的（还有效的孵化授权）：受理了授权生效（30 天），不受理
        授权结束、名称转回自助（SDD 0046 第四节，decisions 10-29）。这一版原来是
        「自助（不受理）」的（作者后来转了平台），换成这张正式的。

        先锁这个名称：两个运营同时写同一版，后到的回 409 而不是撞主键（Kimi 评审
        第 6 条）。
        """
        rid = parse_id(request_id, "request")
        async with self.repo.transaction():
            await self.repo.lock_request(rid)
            if await self.repo.get_version(rid, version) is None:
                raise NotFoundError("No such version", code="version_not_found")
            now = self.now()
            grant = live_incubation(list(await self.repo.list_grants([rid])), now)
            if grant is None:
                raise NotFoundError("No such request", code="request_not_found")
            existing = await self.repo.get_acceptance(rid, version)
            if existing is not None and existing.kind != "self":
                raise _conflict(
                    "This version already has its acceptance", "already_decided"
                )
            record = AcceptanceRecord(
                request_id=rid,
                version=version,
                accepted=body.accepted,
                checks={k: v.model_dump(mode="json") for k, v in body.checks.items()},
                reason=body.reason.strip() or None,
                decided_by=decider,
                decided_at=now,
            )
            if existing is None:
                await self.repo.add_acceptance(record)
            else:
                await self.repo.replace_acceptance(record)
            if grant.status == "pending":
                if body.accepted:
                    await self.repo.update_grant(
                        grant.id,
                        status="active",
                        decided_by=decider,
                        decided_at=now,
                        expires_at=grant_expiry(now),
                    )
                else:
                    await self.repo.update_grant(
                        grant.id,
                        status="declined",
                        decided_by=decider,
                        decided_at=now,
                        reason=record.reason,
                    )
                    await self.repo.set_incubation_mode(rid, "self")
            return record

    async def _locked(self, draft_id: str | uuid.UUID) -> DraftRecord:
        draft = await self.repo.get_draft(parse_id(draft_id), for_update=True)
        if draft is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        return draft

    async def _move(self, draft: DraftRecord, target: str, **changes: Any) -> None:
        if not can_move(draft.status, target):
            raise _conflict(
                f"A {draft.status} draft cannot become {target}", "draft_state"
            )
        await self.repo.update_draft(draft.id, status=target, **changes)

    async def request_user_review(self, draft_id: str, requester: str) -> None:
        """请用户审核这位准专家（web端「审核」里一条待办）。只在待测、还没请过时。"""
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status != "pending_test":
                raise _conflict("Only a pending draft is reviewed", "draft_state")
            if draft.user_review:
                raise _conflict("The user was already asked", "review_requested")
            await self.repo.update_draft(
                draft.id,
                user_review={
                    "requested_by": requester,
                    "requested_at": self.now().isoformat(),
                },
            )

    async def answer_user_review(
        self, owner_subject: str, draft_id: str, body: UserReviewIn
    ) -> DraftRecord:
        """用户审核准专家：行 / 不行（不行要说哪里不对，不再测，等再孵化）。"""
        draft, _ = await self.my_draft(owner_subject, draft_id)
        async with self.repo.transaction():
            current = await self._locked(draft.id)
            if review_state(current.user_review) != "pending":
                raise _conflict("Nothing to review", "review_not_pending")
            await self.repo.update_draft(
                current.id,
                user_review={
                    **(current.user_review or {}),
                    "ok": body.ok,
                    "note": body.note.strip(),
                    "answered_at": self.now().isoformat(),
                },
            )
        found = await self.repo.get_draft(draft.id)
        assert found is not None
        return found

    async def review_rules(self, draft_id: str, reviewer: str) -> None:
        """发起测试前逐条核过规则出处（C-L1）。只在待测时做。"""
        async with self.repo.transaction():
            draft = await self._locked(draft_id)
            if draft.status != "pending_test":
                raise _conflict("Only a pending draft is reviewed", "draft_state")
            await self.repo.update_draft(
                draft.id, rules_reviewed_by=reviewer, rules_reviewed_at=self.now()
            )

    # ---------- 管理端：测试 ----------
    async def start_test(
        self,
        draft_id: str,
        *,
        timeout_seconds: int,
        budget_limit: str | None = None,
        starter: str | None = None,
        runner: dict[str, str] | None = None,
    ) -> TestRecord:
        """拿一位准专家按用例跑一次：新编号。都在作者的账号下跑（decisions ⑯）：runner
        给了就是作者自助，在他选的电脑上（{environment_id, workspace_root}，SDD 0046
        第三节）；没给是运营发起的平台测试，用作者上次自助测试用过的电脑与目录，测试仍记
        平台模式（谁发起、谁看结果不变）。

        费用上限：自助的是作者这次填的；平台的是作者在授权里设的（`platform_budget`）。"""
        mode = "self" if runner else "platform"
        await self._key_ready(draft_id)
        if runner is None:
            budget_limit = await self.platform_budget(
                await self.operator_draft(draft_id)
            )
            runner = await self._authors_computer(draft_id)
        assert budget_limit is not None
        async with self.repo.transaction():
            found = await self.repo.get_draft(parse_id(draft_id))
            if found is None:
                raise NotFoundError("No such draft", code="draft_not_found")
            await self.repo.lock_request(found.request_id)
            draft = await self._locked(found.id)
            if draft.status != "pending_test":
                raise _conflict("Only a pending draft is tested", "draft_state")
            if not draft.rules_reviewed_by:
                raise _conflict(
                    "Review every rule source before testing", "rules_not_reviewed"
                )
            state = review_state(draft.user_review)
            if state == "pending":
                raise _conflict("The user has not reviewed it yet", "review_pending")
            if state == "not_ok":
                raise _conflict(
                    "The user said this draft is not right; incubate again",
                    "review_rejected",
                )
            experts = await self.repo.list_experts([draft.request_id])
            if any(str(e.version) == draft.version for e in experts):
                raise _conflict(
                    "This draft aims at a version that is already an expert; "
                    "incubate again",
                    "version_taken",
                )
            tests = await self.repo.list_tests(request_ids=[draft.request_id])
            if any(t.status in ACTIVE_TESTS for t in tests):
                raise _conflict(
                    "Another test of this knowbot is running", "test_running"
                )
            request = await self.repo.get_request(draft.request_id)
            version = await self.repo.get_version(
                draft.request_id, draft.request_version
            )
            assert request is not None and version is not None
            if not version.cases:
                # 从 knowbench 转来的问题申请先不带用例（第 4.9 节第 6 条）
                raise _conflict(
                    "This idea version has no test cases; add them and publish again",
                    "cases_missing",
                )
            test_id = uuid.uuid4()
            run = RunRequestV1.model_validate(
                {
                    "contract": CONTRACT,
                    "run_id": f"knowlab-test-{test_id}",
                    "owner_subject": request.owner_subject,
                    "manifest": draft.manifest,
                    "cases": cases_for_test(version.cases, request.case_answers),
                    "budget": {"limit": budget_limit},
                    "timeout_seconds": timeout_seconds,
                    "max_wait_seconds": MAX_WAIT_SECONDS,
                    "runner": {"kind": "user", **runner},
                }
            )
            test = await self.repo.create_test(
                TestRecord(
                    id=test_id,
                    request_id=draft.request_id,
                    number=max((t.number for t in tests), default=0) + 1,
                    draft_id=draft.id,
                    status="queued",
                    run_id=run.run_id,
                    started_at=self.now(),
                    budget_limit=budget_limit,
                    timeout_seconds=timeout_seconds,
                    started_by=starter,
                    mode=mode,
                    environment_id=runner["environment_id"],
                    workspace_root=runner["workspace_root"],
                )
            )
            await self._move(draft, "testing")
        grants: list[str] = []
        if self.test_data is not None:
            # 带测试集发起：有的用例每条签一张授权、带上钉住的几份（不跑第 0 步）
            try:
                cases, set_version, grants = await self.test_data.with_test_set(
                    request,
                    [c.model_dump(mode="json") for c in run.cases],
                    run_id=run.run_id,
                    ttl_seconds=min(
                        GRANT_MAX_SECONDS,
                        timeout_seconds + MAX_WAIT_SECONDS + GRANT_MARGIN_SECONDS,
                    ),
                )
            except AppException as exc:
                await self._end_on_platform(test.id, f"test grant refused: {exc.code}")
                raise
            if set_version is not None:
                run = run.model_copy(
                    update={"cases": tuple(RunCase.model_validate(c) for c in cases)}
                )
                async with self.repo.transaction():
                    await self.repo.update_test(
                        test.id, test_set_version=set_version, case_grants=grants
                    )
        try:
            result = await self.runtimes.start(request.runtime, run)
        except AppException as exc:
            await self._end_on_platform(test.id, f"start failed: {exc.code}")
            if self.test_data is not None:
                await self.test_data.revoke(grants)
            raise
        if result.final:
            await self._finish(test.id, result)
        else:
            await self._progress(test.id, result)
        started = await self.repo.get_test(test.id)
        assert started is not None
        return started

    async def start_self_test(
        self,
        owner_subject: str,
        draft_id: str,
        *,
        environment_id: str,
        workspace_root: str,
        budget_limit: str,
        timeout_seconds: int,
    ) -> TestRecord:
        """自助测试（SDD 0046 第三节）：用户本人在他自己的电脑上、用他的模型 key 跑，
        费用上限他自己设。只有自己的准专家。"""
        await self.my_draft(owner_subject, draft_id)
        return await self.start_test(
            draft_id,
            budget_limit=budget_limit,
            timeout_seconds=timeout_seconds,
            starter=owner_subject,
            runner={"environment_id": environment_id, "workspace_root": workspace_root},
        )

    async def _key_ready(self, draft_id: str) -> None:
        found = await self.repo.get_draft(parse_id(draft_id))
        request = (
            None if found is None else await self.repo.get_request(found.request_id)
        )
        if found is None or request is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        await require_model_key(self.runtimes, request)

    async def _authors_computer(self, draft_id: str) -> dict[str, str]:
        """平台测试在哪跑（decisions ⑯）：作者上次自助测试用过的电脑与目录，先向运行方
        列他的电脑，确认那台还在、目录还在白名单里（不要求此刻在线：不在线的测试停在
        「等电脑」）。作者从没接入过电脑、或那台不在了，不能发起。"""
        found = await self.repo.get_draft(parse_id(draft_id))
        request = (
            None if found is None else await self.repo.get_request(found.request_id)
        )
        if found is None or request is None:
            raise NotFoundError("No such draft", code="draft_not_found")
        last = await self.repo.last_self_test(request.owner_subject)
        if last is not None:
            computers = await self.runtimes.computers(
                request.runtime, request.owner_subject
            )
            there = next(
                (
                    c
                    for c in computers
                    if c.get("environment_id") == last.environment_id
                ),
                None,
            )
            if there is not None and last.workspace_root in list(
                there.get("roots") or []
            ):
                assert last.environment_id is not None
                assert last.workspace_root is not None
                return {
                    "environment_id": last.environment_id,
                    "workspace_root": last.workspace_root,
                }
        raise _conflict(
            "The author has no usable computer yet: platform tests run on the "
            "computer and folder of the author's last own test",
            "author_has_no_computer",
        )

    async def my_computers(self, owner_subject: str, draft_id: str) -> dict[str, Any]:
        """选电脑（SDD 0046 第三节）：他接入过的电脑，与上次自助测试用的那台
        与根目录。"""
        _, request = await self.my_draft(owner_subject, draft_id)
        found = await self.runtimes.computers(request.runtime, owner_subject)
        last = await self.repo.last_self_test(owner_subject)
        return {
            "computers": found,
            "last": {
                "environment_id": last.environment_id,
                "workspace_root": last.workspace_root,
            }
            if last
            else None,
            # 模型 key 填了没有：没填页面先拦（SDD 0046 第三节），None 是说不准
            "model_key": await self.runtimes.model_key(request.runtime, owner_subject),
        }

    async def refresh_mine(self, owner_subject: str, test_id: str) -> TestRecord:
        """自己的测试刷新一次（自助测试的页面轮询）。"""
        test, request = await self.test_with_request(test_id)
        if request.owner_subject != owner_subject:
            raise NotFoundError("No such test", code="test_not_found")
        return await self.refresh(str(test.id))

    async def refresh(self, test_id: str) -> TestRecord:
        """问运行方结果；有了结论就记下（通过的出专家）。测完了还缺的产出补取。"""
        test, request = await self.test_with_request(test_id)
        if test.status in ACTIVE_TESTS:
            if self.on_refresh is not None:
                await self.on_refresh(test, request)  # 先补送答过但没送到的答案
            if self.test_data is not None:
                await self.test_data.advance(test, request)  # 在取的数据办完了没有
            result = await self.runtimes.status(request.runtime, test.run_id)
            if result.run_id != test.run_id:
                raise _conflict("The runtime answered another run", "run_mismatch")
            if result.final:
                await self._finish(test.id, result)
            else:
                await self._progress(test.id, result)
            await self._collect_outputs(test, request, result)
        elif test.report:
            await self._collect_outputs(
                test, request, RunResultV1.model_validate(test.report)
            )
        found = await self.repo.get_test(test.id)
        assert found is not None
        return found

    async def refresh_active(self) -> int:
        """后台定时刷新（作者不开页面也更新）：在跑的测试每个刷新一次，同页面轮询
        （refresh）。一个出错记一条日志，不挡别的。回刷新了几个。"""
        done = 0
        tests = await self.repo.list_tests(statuses=ACTIVE_TESTS)
        for test in tests[:REFRESH_BATCH]:
            try:
                await self.refresh(str(test.id))
            except AppException as exc:
                log.warning(
                    "knowbot test not refreshed test=%s code=%s", test.id, exc.code
                )
                continue
            done += 1
        return done

    async def _collect_outputs(
        self, test: TestRecord, request: RequestRecord, result: RunResultV1
    ) -> None:
        """有结论的用例各取一次产出（SDD 0037），一次刷新最多取 OUTPUTS_PER_REFRESH 条。

        只为同意了新说明的创意取。运行方不可达、还是旧版：这一轮就停，下次刷新再
        取；回包不合契约、答错用例：确定的错，记下原因不再取；运行方说没有：记一句
        原因（审读 10-19）。
        """
        if result.run_id != test.run_id:
            return
        if request.consent_version < CONSENT_VERSION:
            return
        have = await self.repo.test_output_cases(test.draft_id, test.run_id)
        todo = [c.case_id for c in result.cases if c.case_id not in have]
        for case_id in todo[:OUTPUTS_PER_REFRESH]:
            note = None
            try:
                found = await self.runtimes.outputs(
                    request.runtime, test.run_id, case_id
                )
            except ServiceUnavailableError as exc:
                log.warning(
                    "knowbot test outputs not fetched test=%s case=%s code=%s",
                    test.id,
                    case_id,
                    exc.code,
                )
                return
            except AppException as exc:
                found, note = None, f"运行方回的产出不合约定（{exc.code}），没有存"
            async with self.repo.transaction():
                await self.repo.put_test_output(
                    TestOutputRecord(
                        draft_id=test.draft_id,
                        run_id=test.run_id,
                        case_id=case_id,
                        outputs=None
                        if found is None
                        else found.model_dump(mode="json"),
                        note=note or (NO_OUTPUTS if found is None else None),
                    )
                )

    async def expire_test_outputs(self) -> int:
        """上架或被下一位准专家取代满 90 天的：清掉产出正文（定时任务每小时一次）。"""
        async with self.repo.transaction():
            return await self.repo.expire_test_outputs(
                self.now() - timedelta(days=OUTPUTS_KEEP_DAYS)
            )

    async def _progress(self, test_id: uuid.UUID, result: RunResultV1) -> None:
        """还没结论：记下进度；在等人回答的，把问题记进待回答（有人值守）。"""
        async with self.repo.transaction():
            test = await self.repo.get_test(test_id, for_update=True)
            if test is None or test.status not in ACTIVE_TESTS:
                return
            if test.run_id != result.run_id:
                return
            await self.repo.update_test(
                test.id,
                status=status_from_run(result.status),
                report=result.model_dump(mode="json"),
            )
            if result.questions:
                await self.repo.add_questions(
                    test.draft_id,
                    result.run_id,
                    [
                        # 自助测的问题都归作者本人，运营看不到（SDD 0046 第四节）。工具
                        # 审批的 route 照旧为空（库里的约束不许它转给谁）：自助测的由
                        # 作者本人答，看的是测试的模式（knowbot_questions）
                        q.model_dump(mode="json") | {"route": first_route(q.kind)}
                        for q in result.questions
                    ],
                )

    async def _finish(self, test_id: uuid.UUID, result: RunResultV1) -> None:
        """有了结论：通过的出专家（版本号就是准专家的 x），没通过的准专家记没通过，
        平台出错的准专家回到待测。"""
        test = await self.repo.get_test(test_id)
        if test is None:
            return
        async with self.repo.transaction():
            await self.repo.lock_request(test.request_id)
            test = await self.repo.get_test(test_id, for_update=True)
            assert test is not None
            if test.status not in ACTIVE_TESTS or test.run_id != result.run_id:
                return  # 已经结束了：旧结果不算数
            status = status_from_run(result.status)
            await self.repo.update_test(
                test.id,
                status=status,
                report=result.model_dump(mode="json"),
                finished_at=self.now(),
                error=f"runtime: {result.status}"
                if status == "platform_error"
                else None,
            )
            draft = await self._locked(test.draft_id)
            if status == "passed":
                await self._move(draft, "passed")
                await self.repo.create_expert(
                    ExpertRecord(
                        id=uuid.uuid4(),
                        request_id=test.request_id,
                        version=int(draft.version),
                        draft_id=draft.id,
                        test_id=test.id,
                        created_at=self.now(),
                    )
                )
            elif status == "failed":
                await self._move(draft, "failed")
            else:
                await self._move(draft, "pending_test")
            if test.mode == "platform" and status in ("passed", "failed"):
                # 交平台测「这一次」测完了：测试授权结束（平台出错的可以再测）
                for g in await self.repo.list_grants([test.request_id]):
                    if (
                        g.kind == "test"
                        and g.draft_id == draft.id
                        and g.status == "active"
                    ):
                        await self.repo.update_grant(
                            g.id, status="ended", ended_at=self.now()
                        )
        await self._wrap_up(test.id)

    async def _wrap_up(self, test_id: uuid.UUID) -> None:
        """测试结束（清单 v2）：每条用例签的授权撤掉，作者确认过的记成测试集新一版。"""
        if self.test_data is None:
            return
        test = await self.repo.get_test(test_id)
        if test is None:
            return
        await self.test_data.revoke(test.case_grants)
        await self.test_data.finish(test)

    async def _end_on_platform(self, test_id: uuid.UUID, error: str) -> None:
        """平台出错结束这一次：测试记平台出错，准专家回到待测（再测是新编号）。"""
        async with self.repo.transaction():
            test = await self.repo.get_test(test_id, for_update=True)
            if test is None or test.status not in ACTIVE_TESTS:
                return
            await self.repo.update_test(
                test.id, status="platform_error", error=error, finished_at=self.now()
            )
            draft = await self._locked(test.draft_id)
            if draft.status == "testing":
                await self._move(draft, "pending_test")

    async def reset_stuck(self, test_id: str) -> None:
        """在跑的超过超时（再宽限 5 分钟）还没结论：记平台出错（超时）结束这一次。"""
        test, _ = await self.test_with_request(test_id)
        if test.status not in ACTIVE_TESTS:
            raise _conflict("Only a running test is ended", "test_state")
        # 等人回答的时间不计入超时：加上已经等的；正在等的，再给足最长等待
        report = test.report or {}
        waited = int(report.get("waited_seconds") or 0)
        if report.get("status") == "waiting":
            waited += MAX_WAIT_SECONDS
        deadline = (
            test.started_at
            + timedelta(seconds=(test.timeout_seconds or 0) + waited)
            + STUCK_GRACE
        )
        if self.now() < deadline:
            raise _conflict("The test is still within its time", "not_stuck")
        await self._end_on_platform(test.id, "timed out")
        await self._wrap_up(test.id)
