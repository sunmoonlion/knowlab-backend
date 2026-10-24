"""创意、受理、孵化、测试（SDD 0027、0037 第四节）。

专家的两道门与上架在 knowbot_experts。

没有退回：每个过程跑一次出一个结果，结果定了不改。

- 申请与修改（web端，用户）：申请出创意 v1，改一次出一个新版本；名称、运行方一生不变。
- 受理（管理端）：给某一版写受理单，查三样；不受理写理由，用户改创意出新版本再受理。
- 孵化（管理端，过渡做法：贴清单）：只能选受理了的版本；规则出处只认创意版本里的句子
  （补充方法 0035 取消，旧的留作历史、只读）。可以请用户审核：等用户答的不能测，用户说
  不行的不再测。
- 测试（管理端发起，平台跑）：每次一个新编号；同一个名称同时只有一个在跑。平台出错
  （运行方报错、超时、开跑失败、运营判定卡住）结束这一次，准专家回到待测，再测是新
  编号。通过即出专家，版本号就是准专家的 x；x 已经出过专家的准专家不许再测（再孵化）。

发起测试先建测试、把准专家标成测试中并提交，再调运行方；运行方按 run_id 幂等，重试安全。
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
from typing import Any

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
    DatasetRecord,
    DraftRecord,
    ExpertRecord,
    KnowlabRepository,
    KnowledgeBase,
    RequestRecord,
    RequestVersionRecord,
    RuntimeTestClient,
    TestOutputRecord,
    TestRecord,
)
from app.domain.knowbots.data_needs import bound_problems, format_of, parse_needs
from app.domain.knowbots.datasets import binding_problems
from app.domain.knowbots.drafts import (
    CONSENT_VERSION,
    OUTPUTS_KEEP_DAYS,
    KnowbotRequestIn,
    amendment_refs,
    amendment_status,
    can_move,
    draft_manifest_problems,
)
from app.domain.knowbots.lifecycle import (
    ACTIVE_TESTS,
    AcceptanceIn,
    RevisionIn,
    UserReviewIn,
    review_state,
    status_from_run,
)
from app.domain.knowbots.test_run import (
    CONTRACT,
    DataGrant,
    GrantedData,
    RunRequestV1,
    RunResultV1,
    first_route,
)

log = logging.getLogger(__name__)

OWNER = re.compile(r"^[A-Za-z0-9._:@-]{1,512}$")
NO_OUTPUTS = "这次测试没有取产出：运行方没有这条用例的成稿与交回物"
OUTPUTS_PER_REFRESH = 10  # 一次刷新最多取几条用例的产出，其余下次刷新再取
STUCK_GRACE = timedelta(minutes=5)
# 测试运行授权的时限：测试的超时加最长等人，再留一小时；knowbase 最长给 7 天
GRANT_MARGIN_SECONDS = 3600
GRANT_MAX_SECONDS = 7 * 24 * 3600
# 一个问题最多等多久（所有者定 3 天）；过了运行方把那条用例记 needs_human
MAX_WAIT_SECONDS = 3 * 24 * 3600
MAX_VERSIONS = 200  # 一个创意最多改这么多版


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


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
    """这一版的用例并上用户答过的开步前提问（用例自己写了的不覆盖）。"""
    merged = copy.deepcopy(cases)
    for case in merged:
        given = answers.get(str(case.get("case_id")))
        if isinstance(given, dict) and given:
            preset = case.setdefault("input", {}).setdefault("answers", {})
            for key, value in given.items():
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
        knowledge: KnowledgeBase | None = None,
    ) -> None:
        self.repo = repo
        self.runtimes = runtimes
        self.now = now
        self.on_refresh = on_refresh
        # 配了 knowbase：测试读绑定的数据凭测试运行授权（SDD 0042 第七节）
        self.knowledge = knowledge

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
            return await self.repo.create_request(
                owner_subject=owner_subject,
                title=body.title,
                runtime=body.runtime,
                idea=body.idea.model_dump(mode="json", exclude_none=True),
                cases=[c.model_dump(mode="json") for c in body.cases],
                consent_version=CONSENT_VERSION,
            )

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
            return await self.repo.add_version(
                request_id=request.id,
                version=current.latest_version + 1,
                idea=body.idea.model_dump(mode="json", exclude_none=True),
                cases=[c.model_dump(mode="json") for c in body.cases],
                consent_version=CONSENT_VERSION,
                created_by=owner_subject,
            )

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
        """给创意的某一版写受理单。一个版本一张，写了不改。"""
        rid = parse_id(request_id, "request")
        async with self.repo.transaction():
            if await self.repo.get_version(rid, version) is None:
                raise NotFoundError("No such version", code="version_not_found")
            if await self.repo.get_acceptance(rid, version) is not None:
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
                decided_at=self.now(),
            )
            await self.repo.add_acceptance(record)
            return record

    # ---------- 管理端：孵化 ----------
    async def add_draft(
        self, request_id: str, manifest: object, request_version: int | None = None
    ) -> DraftRecord:
        """过渡做法：贴清单建准专家。只能选受理了的版本（不选就是最新受理了的）。

        版本号 x = 已有专家的最大版本号 + 1，修订号 y 在这个名称里递增。
        """
        rid = parse_id(request_id, "request")
        async with self.repo.transaction():
            request = await self.repo.get_request(rid)
            if request is None:
                raise NotFoundError("No such request", code="request_not_found")
            await self.repo.lock_request(rid)
            accepted = sorted(
                a.version for a in await self.repo.list_acceptances([rid]) if a.accepted
            )
            if request_version is None:
                request_version = accepted[-1] if accepted else None
            if request_version is None or request_version not in accepted:
                raise _conflict(
                    "Incubate only from an accepted version", "version_not_accepted"
                )
            drafts = await self.repo.list_drafts(request_id=rid)
            experts = await self.repo.list_experts([rid])
            version = str(max((e.version for e in experts), default=0) + 1)
            revision = max((d.revision for d in drafts), default=0) + 1
            problems = draft_manifest_problems(
                manifest, runtime=request.runtime, version=version
            )
            if not problems:
                assert isinstance(manifest, dict)
                # 规则出处只认创意版本里的句子：补充方法已取消（SDD 0037 第三节）
                cited, broken = amendment_refs(manifest)
                if cited or broken:
                    problems = [
                        "rule sources cite the idea version; amendments are retired"
                    ]
            if not problems:
                # 测试只读这一版绑定的、这个用户自己的数据（C-L2）；写了数据需求声明的，
                # 绑定按声明核（SDD 0042 第五节）
                assert isinstance(manifest, dict)
                datasets = await self.repo.list_datasets(request.owner_subject)
                owned = {str(d.id): (d.version, d.sha256) for d in datasets}
                problems = binding_problems(manifest.get("data_bindings"), owned)
                if not problems:
                    problems = bound_problems(
                        parse_needs(manifest.get("data_needs"))[0],
                        manifest.get("data_bindings"),
                        {str(d.id): format_of(d.content_type) for d in datasets},
                    )
            if problems:
                raise BadRequestError("; ".join(problems), code="manifest_invalid")
            assert isinstance(manifest, dict)
            return await self.repo.create_draft(
                request_id=rid,
                request_version=request_version,
                revision=revision,
                version=version,
                manifest=manifest,
            )

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
        budget_limit: str,
        timeout_seconds: int,
        starter: str | None = None,
    ) -> TestRecord:
        """拿一位准专家按用例跑一次：新编号。"""
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
            needs = await self._grant_needs(request.owner_subject, draft.manifest)
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
                )
            )
            await self._move(draft, "testing")
        grant = None
        if needs is not None:
            try:
                grant = await self._data_grant(request.owner_subject, run, *needs)
            except AppException as exc:
                # 签不出就不开跑（SDD 0042「申请不到不开跑」）：这一次记平台出错
                await self._end_on_platform(test.id, f"test grant refused: {exc.code}")
                raise
            run = run.model_copy(update={"data_grant": grant})
            async with self.repo.transaction():
                await self.repo.update_test(test.id, grant_id=grant.grant_id)
        try:
            result = await self.runtimes.start(request.runtime, run)
        except AppException as exc:
            await self._end_on_platform(test.id, f"start failed: {exc.code}")
            await self._revoke(grant.grant_id if grant else None)
            raise
        if result.final:
            await self._finish(test.id, result)
        else:
            await self._progress(test.id, result)
        started = await self.repo.get_test(test.id)
        assert started is not None
        return started

    async def _grant_needs(
        self, owner_subject: str, manifest: dict[str, Any]
    ) -> tuple[list[DatasetRecord], dict[str, Any]] | None:
        """这次测试要不要签测试运行授权（SDD 0042 第七节，Astra 评审 F02）。

        三种：没绑数据，不签（None）；绑的都还没搬进 knowbase（没有资料编号），照旧经
        knowlab 读，不签（None，第 1 版停用后这条路撤掉）；有搬进去的，就必须签，回
        （要读的那几份，测试同意）。缺一样（搬了一半、数据变了、没有测试同意、knowbase
        没配）就报明原因、不开跑，不退回旧读路。
        """
        raw = manifest.get("data_bindings")
        bindings = (
            [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
        )
        records = []
        for binding in bindings:
            try:
                found = await self.repo.get_dataset(
                    uuid.UUID(str(binding.get("dataset_id")))
                )
            except ValueError:
                found = None
            if (
                found is None
                or found.owner_subject != owner_subject
                or found.deleted_at is not None
                or found.sha256 != binding.get("sha256")
            ):
                raise _conflict(
                    "A bound dataset is gone or changed; incubate again",
                    "data_binding_stale",
                )
            records.append(found)
        imported = [d for d in records if d.item_id]
        if not imported:
            return None
        if len(imported) != len(records):
            raise _conflict(
                "Some bound data is not in knowbase yet; run import_datasets first",
                "data_not_imported",
            )
        if self.knowledge is None:
            raise ServiceUnavailableError(
                "knowbase is not configured", code="knowbase_unconfigured"
            )
        consent = await self.repo.get_test_consent(owner_subject)
        if consent is None:
            raise _conflict(
                "The owner has not consented to testing with this data",
                "test_consent_missing",
            )
        return records, consent

    async def _data_grant(
        self,
        owner_subject: str,
        run: RunRequestV1,
        records: list[DatasetRecord],
        consent: dict[str, Any],
    ) -> DataGrant:
        """向 knowbase 签这次测试读绑定数据的测试运行授权；拒了、不可达照原样抛出。"""
        assert self.knowledge is not None
        ttl = min(
            GRANT_MAX_SECONDS,
            run.timeout_seconds + run.max_wait_seconds + GRANT_MARGIN_SECONDS,
        )
        grant_id = await self.knowledge.test_grant(
            owner=owner_subject,
            consent_id=consent["consent_id"],
            ref=run.run_id,
            items=[{"item_id": str(d.item_id), "sha256": d.sha256} for d in records],
            ttl_seconds=ttl,
        )
        return DataGrant(
            grant_id=grant_id,
            items=tuple(
                GrantedData(
                    dataset_id=str(d.id),
                    item_id=str(d.item_id),
                    name=d.name,
                    media_type=d.content_type,
                    size_bytes=d.size_bytes,
                    sha256=d.sha256,
                )
                for d in records
            ),
        )

    async def _revoke(self, grant_id: str | None) -> None:
        """测试结束撤运行授权；撤不成只记一行（授权到期自己失效）。"""
        if grant_id is None or self.knowledge is None:
            return
        try:
            await self.knowledge.revoke_test_grant(grant_id)
        except AppException as exc:
            log.warning("test grant not revoked grant=%s code=%s", grant_id, exc.code)

    async def refresh(self, test_id: str) -> TestRecord:
        """问运行方结果；有了结论就记下（通过的出专家）。测完了还缺的产出补取。"""
        test, request = await self.test_with_request(test_id)
        if test.status in ACTIVE_TESTS:
            if self.on_refresh is not None:
                await self.on_refresh(test, request)  # 先补送答过但没送到的答案
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
        await self._revoke(test.grant_id)

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
        await self._revoke(test.grant_id)
