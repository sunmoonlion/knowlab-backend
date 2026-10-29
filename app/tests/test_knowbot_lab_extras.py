"""发版前补齐 L5（SDD 0046 第四、九节；专家孵化方案第 8.3、8.4、十八节）：

- 模型 key 没填，自助测试、平台测试、AI 一轮发起前就拦；运行方说不准的不拦；
- 带上最近一次测试的报告让 AI 改（只有每一步的结果，不带期望值、成稿与数据）；
- 一键接受一步的全部建议（核不上的跳过）；
- 作者不开页面，后台也刷新没结束的孵化一轮与在跑的测试；
- 平台视图写出平台在问作者什么（自助的不算）。

内存仓储、假运行方、假孵化。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import ProposalRecord, TestRecord
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.incubation import IncubationResultV1
from tests.knowbot_fakes import (
    COMPUTER,
    COMPUTER_ROOT,
    OWNER,
    FakeIncubator,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
    manifest,
    own_computer,
    request_body,
    result,
)

HANDBACK = {
    "sources": [
        {"path": "steps/scan/why", "kind": "proposal"},
        {"path": "steps/scan/rules/r2", "kind": "idea", "quote": "不在创意里的话"},
        {"path": "steps/scan/title", "kind": "proposal"},
        {"path": "steps/other/title", "kind": "proposal"},
    ],
    "gaps": [{"id": "g1", "question": "看几年？", "proposal": "近三年"}],
    "suggestions": [
        {
            "id": "a1",
            "path": "steps/scan/why",
            "op": "set",
            "value": "先知道手上有什么。",
            "reason": "写明为什么",
        },
        {
            "id": "a2",
            "path": "steps/scan/rules/r2",
            "op": "add",
            "value": {"text": "只看 A 股。"},
            "reason": "范围",
        },
        {
            "id": "a3",
            "path": "steps/scan/title",
            "op": "set",
            "value": "数据摸底",
            "reason": "起个名字",
        },
        {
            "id": "a4",
            "path": "steps/other/title",
            "op": "set",
            "value": "别的一步",
            "reason": "不在这一步",
        },
    ],
}


class Wired:
    def __init__(self) -> None:
        self.repo = MemoryKnowlab()
        self.runtime = FakeRuntime()
        self.incubator = FakeIncubator()
        self.svc = KnowbotCreationService(self.repo, self.runtime)
        self.inc = KnowbotIncubationService(self.repo, self.incubator, self.runtime)
        self.works = KnowbotWorkdraftService(self.repo)
        self.board = KnowbotBoard(self.repo)

    async def own(self):
        """一个自己来的名称（不过平台）。"""
        return await self.svc.submit(
            OWNER,
            KnowbotRequestIn.model_validate(
                request_body(mode="self", budget_limit=None)
            ),
        )

    async def self_draft(self, request_id: uuid.UUID, version: str = "1"):
        draft = await self.repo.create_draft(
            request_id=request_id,
            request_version=1,
            revision=1,
            version=version,
            manifest=manifest(version),
            mode="self",
        )
        await self.repo.update_draft(draft.id, rules_reviewed_by="system:workdraft")
        return draft

    def hand_back(self, record: Any, handback: dict[str, Any]) -> None:
        self.incubator.results[record.run_id] = IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": record.run_id,
                "status": "succeeded",
                "handback": handback,
            }
        )


@pytest.fixture
def w() -> Wired:
    wired = Wired()
    own_computer(wired.repo)
    return wired


# ---------- 模型 key ----------
async def test_no_model_key_stops_a_round_before_it_starts(w: Wired) -> None:
    request = await w.own()
    w.runtime.key_ready = False
    with pytest.raises(ConcurrencyConflictError) as raised:
        await w.inc.start(OWNER, str(request.id), action="draft")
    assert raised.value.code == "model_key_missing"
    assert w.incubator.started == [] and w.runtime.key_asked == [OWNER]
    assert await w.repo.list_incubations(request_id=request.id) == []
    # 运行方说不准（还没有这个接口）：不拦
    w.runtime.key_ready = None
    await w.inc.start(OWNER, str(request.id), action="draft")
    assert len(w.incubator.started) == 1


async def test_no_model_key_stops_self_and_platform_tests(w: Wired) -> None:
    request = await w.own()
    draft = await w.self_draft(request.id)
    w.runtime.key_ready = False
    with pytest.raises(ConcurrencyConflictError) as raised:
        await w.svc.start_self_test(
            OWNER,
            str(draft.id),
            environment_id=COMPUTER,
            workspace_root=COMPUTER_ROOT,
            budget_limit="5",
            timeout_seconds=600,
        )
    assert raised.value.code == "model_key_missing"
    assert w.runtime.started == [] and await w.repo.list_tests() == []
    # 平台发起的也在作者的账号下、用作者的 key 跑（decisions ⑯）：一样拦
    platform = await w.svc.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await w.svc.accept(str(platform.id), 1, all_clear(), "admin-1")
    plat = await w.repo.create_draft(
        request_id=platform.id,
        request_version=1,
        revision=1,
        version="1",
        manifest=manifest(),
    )
    await w.repo.update_draft(plat.id, rules_reviewed_by="admin-1")
    with pytest.raises(ConcurrencyConflictError) as again:
        await w.svc.start_test(str(plat.id), timeout_seconds=600, starter="admin-1")
    assert again.value.code == "model_key_missing"


async def test_the_computer_lists_say_whether_the_key_is_there(w: Wired) -> None:
    request = await w.own()
    draft = await w.self_draft(request.id)
    w.runtime.key_ready = False
    assert (await w.inc.computers(OWNER, str(request.id)))["model_key"] is False
    assert (await w.svc.my_computers(OWNER, str(draft.id)))["model_key"] is False
    w.runtime.key_ready = None
    assert (await w.inc.computers(OWNER, str(request.id)))["model_key"] is None


# ---------- 带测试报告让 AI 改 ----------
async def _finished_test(w: Wired, request_id: uuid.UUID, number: int, status: str):
    draft = await w.self_draft(request_id, version=str(number))
    run_id = f"knowlab-test-{uuid.uuid4()}"
    report = result(
        run_id,
        status,
        cases=[
            {
                "case_id": "c1",
                "status": "failed" if status == "failed" else "passed",
                "steps": [
                    {
                        "step_id": "s1",
                        "accepted": status != "failed",
                        "failed_checks": ["sourced"] if status == "failed" else [],
                        "details": ["毛利率对不上原表"] if status == "failed" else [],
                    }
                ],
                "expectations": [status != "failed"],
                "note": "第 1 步没过",
            }
        ],
    )
    await w.repo.create_test(
        TestRecord(
            id=uuid.uuid4(),
            request_id=request_id,
            number=number,
            draft_id=draft.id,
            status=status,
            run_id=run_id,
            started_at=datetime(2026, 10, 28, tzinfo=UTC),
            report=report.model_dump(mode="json"),
            mode="self",
        )
    )
    return draft


async def test_revise_can_carry_the_latest_test_report(w: Wired) -> None:
    request = await w.own()
    rid = str(request.id)
    with pytest.raises(ConcurrencyConflictError) as missing:
        await w.inc.start(
            OWNER, rid, action="revise", instruction="照报告改", with_test=True
        )
    assert missing.value.code == "test_report_missing"
    await _finished_test(w, request.id, 1, "passed")
    await _finished_test(w, request.id, 2, "failed")
    await w.inc.start(
        OWNER, rid, action="revise", instruction="照报告改", with_test=True
    )
    (sent,) = w.incubator.started
    report = sent.test_report
    assert report is not None
    assert (report.number, report.status, report.draft_version) == (2, "failed", "2.1")
    (case,) = report.cases
    assert case.question == "分析 2025 年报"  # 作者写的测试问题
    assert case.steps[0].failed_checks == ("sourced",)
    assert case.steps[0].details == ("毛利率对不上原表",)
    (expected,) = case.expectations
    # 期望核没核上，不带期望值本身
    assert expected.model_dump() == {
        "artifact": "summary",
        "path": "gross_margin",
        "kind": "approx",
        "passed": False,
    }
    assert "0.52" not in sent.model_dump_json()


async def test_only_revise_carries_a_report_and_not_by_default(w: Wired) -> None:
    request = await w.own()
    await _finished_test(w, request.id, 1, "failed")
    with pytest.raises(BadRequestError):
        await w.inc.start(OWNER, str(request.id), action="draft", with_test=True)
    await w.inc.start(OWNER, str(request.id), action="revise", instruction="改一改")
    assert w.incubator.started[0].test_report is None
    assert "test_report" not in w.incubator.started[0].model_dump(mode="json")


# ---------- 一键接受一步 ----------
async def test_accepting_a_whole_step_skips_what_does_not_check_out(w: Wired) -> None:
    request = await w.own()
    rid = str(request.id)
    await w.works.save(
        OWNER, rid, revision=0, edits=[("steps", [{"id": "scan"}, {"id": "other"}])]
    )
    started = await w.inc.start(OWNER, rid, action="draft")
    w.hand_back(started, HANDBACK)
    done = await w.inc.refresh(OWNER, rid, str(started.id))
    stored, record, accepted, skipped = await w.inc.accept_step(
        OWNER, rid, str(done.id), "scan", revision=1
    )
    # 这一步上核得上的都接受了；引文核不上的跳过；别的步的不碰
    assert accepted == ["a1", "a3"]
    assert skipped == [{"id": "a2", "code": "suggestion_unverified"}]
    assert stored.revision == 2
    scan, other = stored.body["steps"]
    assert (scan["title"], scan["why"], scan["rules"]) == (
        "数据摸底",
        "先知道手上有什么。",
        [],
    )
    assert other["title"] == ""
    assert stored.sources["steps/scan/why"]["kind"] == "user_confirmed"
    assert set(record.decisions) == {"a1", "a3"}
    # 再点一次：没有可接受的，工作稿不动
    again, _, none, left = await w.inc.accept_step(
        OWNER, rid, str(done.id), "scan", revision=2
    )
    assert none == [] and left == skipped and again.revision == 2


async def test_accepting_a_step_needs_a_finished_round_and_fresh_revision(
    w: Wired,
) -> None:
    request = await w.own()
    rid = str(request.id)
    started = await w.inc.start(OWNER, rid, action="draft")
    with pytest.raises(ConcurrencyConflictError) as running:
        await w.inc.accept_step(OWNER, rid, str(started.id), "scan", revision=0)
    assert running.value.code == "incubation_state"
    w.hand_back(started, HANDBACK)
    await w.inc.refresh(OWNER, rid, str(started.id))
    with pytest.raises(ConcurrencyConflictError) as stale:
        await w.inc.accept_step(OWNER, rid, str(started.id), "scan", revision=3)
    assert stale.value.code == "workdraft_revision"


# ---------- 后台刷新 ----------
async def test_the_sweep_refreshes_rounds_and_tests_nobody_is_watching(
    w: Wired,
) -> None:
    request = await w.own()
    rid = str(request.id)
    started = await w.inc.start(OWNER, rid, action="draft")
    w.hand_back(started, HANDBACK)
    draft = await w.self_draft(request.id)
    test = await w.svc.start_self_test(
        OWNER,
        str(draft.id),
        environment_id=COMPUTER,
        workspace_root=COMPUTER_ROOT,
        budget_limit="5",
        timeout_seconds=600,
    )
    w.runtime.results[test.run_id] = result(test.run_id, "passed")
    assert await w.inc.refresh_active() == 1
    assert await w.svc.refresh_active() == 1
    round_ = await w.repo.get_incubation(started.id)
    assert round_ is not None and round_.status == "succeeded"
    assert len(await w.repo.list_gaps(request_ids=[request.id])) == 1  # 缺口进了待办
    finished = await w.repo.get_test(test.id)
    assert finished is not None and finished.status == "passed"
    # 结束了的不再问
    assert await w.inc.refresh_active() == 0
    assert await w.svc.refresh_active() == 0


async def test_one_failing_round_does_not_stop_the_sweep(w: Wired) -> None:
    first = await w.own()
    second = await w.own()
    a = await w.inc.start(OWNER, str(first.id), action="draft")
    b = await w.inc.start(OWNER, str(second.id), action="draft")

    async def flaky(incubation_id: str) -> IncubationResultV1:
        if incubation_id == a.run_id:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        return IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": incubation_id,
                "status": "failed",
                "error": "no sandbox",
            }
        )

    w.incubator.status = flaky  # type: ignore[method-assign]
    assert await w.inc.refresh_active() == 1
    left = await w.repo.get_incubation(a.id)
    gone = await w.repo.get_incubation(b.id)
    assert left is not None and left.status == "queued"
    assert gone is not None and gone.status == "failed"


# ---------- 平台在问什么 ----------
async def test_the_platform_view_lists_what_the_platform_asked(w: Wired) -> None:
    request = await w.svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    rid = str(request.id)
    await w.svc.accept(rid, 1, all_clear(), "admin-1")
    # 平台人员的提议、请作者审核的准专家、平台模式那一轮的缺口
    await w.repo.add_proposals(
        [
            ProposalRecord(
                id=uuid.uuid4(),
                request_id=request.id,
                path="scope/does",
                op="set",
                value="三张表",
                reason="范围写清楚",
                proposed_by="admin-1",
                proposed_at=datetime(2026, 10, 29, 1, tzinfo=UTC),
            )
        ]
    )
    draft = await w.repo.create_draft(
        request_id=request.id,
        request_version=1,
        revision=1,
        version="1",
        manifest=manifest(),
    )
    await w.svc.request_user_review(str(draft.id), "admin-1")
    platform_round = await w.inc.operator_start(rid, action="draft")
    w.hand_back(platform_round, HANDBACK)
    await w.inc.refresh(OWNER, rid, str(platform_round.id))
    # 作者自己发起的那一轮的缺口不算平台问的
    await w.inc.start(OWNER, rid, action="draft")
    mine = (await w.repo.list_incubations(request_id=request.id))[-1]
    w.hand_back(mine, HANDBACK)
    await w.inc.refresh(OWNER, rid, str(mine.id))

    life = await w.board.life(rid, OWNER)
    asks = await w.board.platform_asks(life)
    kinds = sorted(a["kind"] for a in asks)
    assert kinds == ["gap", "proposal", "review"]
    by_kind = {a["kind"]: a for a in asks}
    assert by_kind["proposal"]["text"] == "范围写清楚"
    assert by_kind["proposal"]["state"] == "open"
    assert by_kind["review"]["label"] == "1.1" and by_kind["review"]["state"] == "open"
    assert by_kind["gap"]["text"] == "看几年？"
    # 作者答了缺口：还列着，记成处理过了、写着答了什么
    gap = await w.repo.get_question(uuid.UUID(by_kind["gap"]["question_id"]))
    assert gap is not None
    await w.repo.update_question(
        gap.id, answer={"option_id": "proposal"}, answered_by="user"
    )
    again = {
        a["kind"]: a
        for a in await w.board.platform_asks(await w.board.life(rid, OWNER))
    }
    assert again["gap"]["state"] == "done" and again["gap"]["answer"] == "按提议"


async def test_a_self_name_has_nothing_from_the_platform(w: Wired) -> None:
    request = await w.own()
    started = await w.inc.start(OWNER, str(request.id), action="draft")
    w.hand_back(started, HANDBACK)
    await w.inc.refresh(OWNER, str(request.id), str(started.id))
    life = await w.board.life(str(request.id), OWNER)
    assert await w.board.platform_asks(life) == []
