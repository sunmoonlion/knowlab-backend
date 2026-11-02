"""AI 孵化的一轮（专家孵化方案第八、九节，第十五节第 4 步）：发起时组的请求（六问的
拼法、skill、一句话问题）、一轮在跑时 409、没电脑 409、取回存交回、出处核对（创意
原句逐字、答复要是答过的缺口）、缺口写进问题表归作者、三种接受动作改格子与记录与出处、
出一版能复核新的引用、运营看不到。内存仓储与假运行方。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.services.knowbot_board import KnowbotBoard, inbox_items
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_questions import (
    AnswerIn,
    KnowbotQuestionService,
)
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.incubation import IncubationResultV1
from app.domain.knowbots.workdraft import IDEA_REF
from tests.knowbot_fakes import (
    COMPUTER,
    COMPUTER_ROOT,
    OWNER,
    FakeIncubator,
    FakeRuntime,
    MemoryKnowlab,
    own_computer,
    request_body,
)

SIX = request_body()["idea"]["answers"]
SIX_SEED = (
    f"问：解决什么\n答：{SIX['solves']}\n\n"
    f"问：不解决什么\n答：{SIX['not_solves']}\n\n"
    f"问：输入是什么\n答：{SIX['inputs']}\n\n"
    f"问：步骤与每步的判断规则\n答：{SIX['steps']}\n\n"
    f"问：输出长什么样\n答：{SIX['outputs']}\n\n"
    f"问：怎样算对\n答：{SIX['correctness']}"
)
STEP = {
    "title": "数据摸底",
    "does": "看清数据有哪些表、哪些报告期。",
    "why": "先知道手上有什么。",
    "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
    "outputs": [{"name": "periods", "type": "list"}],
    "checks": [{"template": "list_min", "params": {"path": "periods"}}],
}


class Wired:
    def __init__(self) -> None:
        self.repo = MemoryKnowlab()
        self.runtime = FakeRuntime()
        self.incubator = FakeIncubator()
        self.svc = KnowbotCreationService(self.repo, self.runtime)
        self.works = KnowbotWorkdraftService(self.repo)
        self.inc = KnowbotIncubationService(self.repo, self.incubator, self.runtime)
        self.questions = KnowbotQuestionService(self.repo, self.runtime)

    async def request(self, owner: str = OWNER, **body: Any):
        return await self.svc.submit(
            owner, KnowbotRequestIn.model_validate(request_body(**body))
        )

    def hand_back(self, record: Any, handback: dict[str, Any], **extra: Any) -> None:
        self.incubator.results[record.run_id] = IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": record.run_id,
                "status": "succeeded",
                "cost": {"spent": "3.5"},
                "task_id": "22222222-2222-4222-8222-222222222222",
                "handback": handback,
            }
            | extra
        )


@pytest.fixture
def w() -> Wired:
    wired = Wired()
    own_computer(wired.repo)
    return wired


FIRST = {
    "sources": [
        {"path": "scope/does", "kind": "idea", "quote": SIX["solves"]},
        {"path": "steps/overview", "kind": "proposal"},
        {"path": "tagline", "kind": "idea", "quote": "我没说过的一句话"},
    ],
    "gaps": [
        {
            "id": "g1",
            "question": "毛利率按哪个口径？",
            "proposal": "营业收入减营业成本",
            "options": ["营业收入口径", "按我的手算"],
        }
    ],
    "suggestions": [
        {
            "id": "s1",
            "path": "scope/does",
            "op": "set",
            "value": "分析一家公司的三张表",
            "reason": "创意里写了解决什么",
        },
        {
            "id": "s2",
            "path": "steps/overview",
            "op": "add",
            "value": STEP,
            "reason": "先摸清数据",
        },
        {
            "id": "s3",
            "path": "tagline",
            "op": "set",
            "value": "按我的方法分析财报",
            "reason": "一句话介绍",
        },
    ],
}


async def _first_round(w: Wired):
    request = await w.request()
    started = await w.inc.start(OWNER, str(request.id), action="draft")
    w.hand_back(started, FIRST)
    done = await w.inc.refresh(OWNER, str(request.id), str(started.id))
    return request, done


# ---------- 发起 ----------
@pytest.mark.asyncio
async def test_a_draft_round_sends_the_six_answers_as_one_seed(w: Wired) -> None:
    request = await w.request()
    record = await w.inc.start(OWNER, str(request.id), action="draft")
    assert (record.round, record.action, record.status) == (1, "draft", "queued")
    assert (record.budget_limit, record.timeout_seconds) == ("20", 3600)
    sent = w.incubator.started[0]
    assert sent.incubation_id == record.run_id == f"knowlab-incubation-{record.id}"
    assert sent.owner_subject == OWNER
    assert (sent.seed.kind, sent.seed.text) == ("six_questions", SIX_SEED)
    assert sent.instruction is None and sent.step_id is None
    assert sent.budget.limit == "20" and sent.timeout_seconds == 3600
    # 不给电脑：用上次自助测试的那台与目录
    assert (sent.runner.environment_id, sent.runner.workspace_root) == (
        COMPUTER,
        COMPUTER_ROOT,
    )
    assert sent.data_formats == () and sent.answers == ()
    # 记着交出去的种子与那一版，取回后照它核
    assert record.seed == {"kind": "six_questions", "text": SIX_SEED}
    assert record.request_version == 1
    # 还没存过的工作稿：建了一份空的，这一轮挂在它上面
    assert w.repo.workdrafts[request.id].id == record.workdraft_id


@pytest.mark.asyncio
async def test_skill_and_question_seeds_go_as_written(w: Wired) -> None:
    skill = await w.request(idea={"kind": "skill", "skill": "# 我的 skill\n先看三张表"})
    await w.inc.start(OWNER, str(skill.id), action="draft")
    question = await w.request(
        idea={"kind": "question", "question": "这家公司未来三年的收入会怎样？"}
    )
    await w.inc.start(OWNER, str(question.id), action="draft", budget_limit="5.5")
    first, second = w.incubator.started
    assert (first.seed.kind, first.seed.text) == ("skill", "# 我的 skill\n先看三张表")
    assert (second.seed.kind, second.seed.text) == (
        "question",
        "这家公司未来三年的收入会怎样？",
    )
    assert second.budget.limit == "5.5"


@pytest.mark.asyncio
async def test_revise_carries_the_instruction_the_step_and_the_workdraft(w) -> None:
    request = await w.request()
    await w.works.save(OWNER, str(request.id), revision=0, edits=[("steps/a", STEP)])
    record = await w.inc.start(
        OWNER,
        str(request.id),
        action="revise",
        instruction="第 1 步拆成两步",
        step_id="a",
        environment_id="33333333-3333-4333-8333-333333333333",
        workspace_root="/elsewhere",
    )
    sent = w.incubator.started[0]
    assert (sent.action, sent.instruction, sent.step_id) == (
        "revise",
        "第 1 步拆成两步",
        "a",
    )
    assert sent.workdraft.body.steps[0].title == "数据摸底"
    assert sent.workdraft.sources["steps/a/title"].kind == "user_text"
    assert sent.runner.workspace_root == "/elsewhere"
    assert (record.workdraft_revision, record.environment_id) == (
        1,
        "33333333-3333-4333-8333-333333333333",
    )


@pytest.mark.asyncio
async def test_bad_starts_are_refused(w: Wired) -> None:
    request = await w.request()
    rid = str(request.id)
    with pytest.raises(BadRequestError):
        await w.inc.start(OWNER, rid, action="revise")  # 改要一句话
    with pytest.raises(BadRequestError):
        await w.inc.start(OWNER, rid, action="draft", instruction="x")
    with pytest.raises(BadRequestError):
        await w.inc.start(OWNER, rid, action="revise", instruction="x", step_id="nope")
    with pytest.raises(BadRequestError):
        await w.inc.start(OWNER, rid, action="draft", budget_limit="0")
    with pytest.raises(BadRequestError):
        await w.inc.start(OWNER, rid, action="draft", environment_id=COMPUTER)
    with pytest.raises(NotFoundError):
        await w.inc.start("someone-else", rid, action="draft")
    assert w.incubator.started == [] and w.repo.incubations == {}


@pytest.mark.asyncio
async def test_one_round_at_a_time_per_workdraft(w: Wired) -> None:
    request = await w.request()
    await w.inc.start(OWNER, str(request.id), action="draft")
    with pytest.raises(ConcurrencyConflictError) as caught:
        await w.inc.start(OWNER, str(request.id), action="draft")
    assert caught.value.code == "incubation_running"
    assert len(w.incubator.started) == 1


@pytest.mark.asyncio
async def test_no_computer_is_a_conflict() -> None:
    w = Wired()  # 作者从没接入过电脑、没测过
    request = await w.request()
    with pytest.raises(ConcurrencyConflictError) as caught:
        await w.inc.start(OWNER, str(request.id), action="draft")
    assert caught.value.code == "author_has_no_computer"
    assert w.repo.incubations == {}
    found = await w.inc.computers(OWNER, str(request.id))
    assert found["last"] is None and found["computers"][0]["environment_id"]


@pytest.mark.asyncio
async def test_unconfigured_or_unreachable_knowbench(w: Wired) -> None:
    request = await w.request()
    w.incubator.on = False
    with pytest.raises(ServiceUnavailableError):
        await w.inc.start(OWNER, str(request.id), action="draft")
    assert w.repo.incubations == {}
    w.incubator.on, w.incubator.fail_start = True, True
    with pytest.raises(ServiceUnavailableError):
        await w.inc.start(OWNER, str(request.id), action="draft")
    (record,) = w.repo.incubations.values()
    assert record.status == "failed" and record.error is not None
    # 失败的不占着：可以再来一轮（第二轮）
    w.incubator.fail_start = False
    again = await w.inc.start(OWNER, str(request.id), action="draft")
    assert again.round == 2


@pytest.mark.asyncio
async def test_the_last_computer_is_the_later_of_test_and_round(w: Wired) -> None:
    request = await w.request()
    other = "44444444-4444-4444-8444-444444444444"
    record = await w.inc.start(
        OWNER,
        str(request.id),
        action="draft",
        environment_id=other,
        workspace_root="/w2",
    )
    await w.inc.cancel(OWNER, str(request.id), str(record.id))
    found = await w.inc.computers(OWNER, str(request.id))
    assert found["last"] == {"environment_id": other, "workspace_root": "/w2"}


# ---------- 取回、核出处、缺口 ----------
@pytest.mark.asyncio
async def test_refresh_stores_the_handback_and_checks_sources(w: Wired) -> None:
    request, done = await _first_round(w)
    assert (done.status, done.spent, done.task_id) == (
        "succeeded",
        "3.5",
        "22222222-2222-4222-8222-222222222222",
    )
    assert done.finished_at is not None and done.handback is not None
    review = done.review or {}
    assert review["s1"] == {"verified": True, "kinds": ["idea"], "problems": []}
    assert review["s2"]["verified"] and review["s2"]["kinds"] == ["proposal"]
    # 引的原句不在发出时那一版创意里：出处核不上
    assert review["s3"]["verified"] is False
    assert review["s3"]["problems"] == [
        {"path": "tagline", "problem": "quote_not_in_idea"}
    ]
    # 结束了的不再问运行方
    asked = len(w.incubator.results)
    again = await w.inc.refresh(OWNER, str(request.id), str(done.id))
    assert again.status == "succeeded" and len(w.incubator.results) == asked


@pytest.mark.asyncio
async def test_gaps_become_my_questions_and_operators_never_see_them(w) -> None:
    request, done = await _first_round(w)
    (gap,) = await w.inc.gaps(done)
    assert (gap.kind, gap.route, gap.draft_id, gap.request_id) == (
        "gap",
        "user",
        None,
        request.id,
    )
    assert gap.proposal == "营业收入减营业成本"
    assert [o["label"] for o in gap.options] == ["营业收入口径", "按我的手算"]
    # 进作者的审核栏（待办）；运营的问题列表里没有
    mine = await w.questions.user_queue(OWNER)
    assert [(q.id, d) for q, d, _ in mine] == [(gap.id, None)]
    lives = await KnowbotBoard(w.repo).lives(OWNER)
    assert [k for _, k, _, _ in inbox_items(lives, [q for q, _, _ in mine])] == [
        "question"
    ]
    assert await w.questions.operator_queue() == []
    assert await w.questions.answered(request.id) == []
    with pytest.raises(NotFoundError):
        await w.questions.answer_as_operator(str(gap.id), AnswerIn(text="x"))
    with pytest.raises(NotFoundError):
        await w.questions.take(str(gap.id), "跑法问题")
    with pytest.raises(NotFoundError):
        await w.questions.answer_as_user(
            "someone-else", str(gap.id), AnswerIn(text="x")
        )
    # 作者答「按提议」：记下，不送运行方
    answered = await w.questions.answer_as_user(
        OWNER, str(gap.id), AnswerIn(option_id="proposal")
    )
    assert answered.answer == {"option_id": "proposal"}
    assert answered.answered_by == "user" and answered.sent_at is None
    assert w.runtime.answers == []
    assert await w.questions.user_queue(OWNER) == []
    assert [q.id for q in await w.questions.answered_by_user(OWNER)] == [gap.id]
    with pytest.raises(ConcurrencyConflictError):
        await w.questions.answer_as_user(OWNER, str(gap.id), AnswerIn(text="再答"))


# ---------- 接受建议 ----------
@pytest.mark.asyncio
async def test_planning_suggestion_only_becomes_author_method_after_acceptance(w):
    from tests.dynamic_method_fixtures import body

    request = await w.request()
    record = await w.inc.start(OWNER, str(request.id), action="draft")
    planning = body()["planning"]
    w.hand_back(
        record,
        {
            "sources": [{"path": "planning", "kind": "proposal"}],
            "suggestions": [
                {
                    "id": "plan",
                    "path": "planning",
                    "op": "set",
                    "value": planning,
                    "reason": "请作者确认方法选择边界",
                }
            ],
        },
    )
    done = await w.inc.refresh(OWNER, str(request.id), str(record.id))
    _, before = await w.works.get(OWNER, str(request.id))
    assert before is None or "planning" not in before.body
    stored, _ = await w.inc.decide(
        OWNER, str(request.id), str(done.id), "plan", action="accept", revision=0
    )
    assert stored is not None and stored.body["planning"] == planning
    source = stored.sources["planning"]
    assert source["kind"] == "user_confirmed"
    entry = w.repo.entries[uuid.UUID(source["ref"].removeprefix("record:"))]
    assert entry.kind == "accept" and entry.actor_subject == OWNER
    assert entry.suggestion_ref == f"{done.id}:plan"


@pytest.mark.asyncio
async def test_accepting_an_idea_suggestion_cites_the_idea_sentence(w) -> None:
    request, done = await _first_round(w)
    stored, record = await w.inc.decide(
        OWNER, str(request.id), str(done.id), "s1", action="accept", revision=0
    )
    assert stored is not None
    assert stored.body["scope"]["does"] == "分析一家公司的三张表"
    source = stored.sources["scope/does"]
    assert source["kind"] == "user_text"
    match = IDEA_REF.match(source["ref"])
    assert match is not None and match.group(1) == "1"
    start, length = int(match.group(2)), int(match.group(3))
    assert SIX_SEED[start : start + length] == SIX["solves"]
    # 记了一条接受记录（哪一轮的哪一条建议），出处引的是创意原句
    (entry,) = w.repo.entries.values()
    assert (entry.kind, entry.path, entry.suggestion_ref) == (
        "accept",
        "scope/does",
        f"{done.id}:s1",
    )
    assert record.decisions["s1"]["state"] == "accepted"
    assert (await w.works.origins(stored))["scope/does"] == "idea"
    # 处理过的不能再处理
    with pytest.raises(ConcurrencyConflictError):
        await w.inc.decide(OWNER, str(request.id), str(done.id), "s1", action="reject")


@pytest.mark.asyncio
async def test_accepting_a_whole_step_gives_every_cell_its_own_source(w) -> None:
    request, done = await _first_round(w)
    stored, _ = await w.inc.decide(
        OWNER, str(request.id), str(done.id), "s2", action="accept", revision=0
    )
    assert stored is not None
    assert [s["id"] for s in stored.body["steps"]] == ["overview"]
    cells = [p for p in stored.sources if p.startswith("steps/overview/")]
    assert sorted(cells) == [
        "steps/overview/checks/0",
        "steps/overview/does",
        "steps/overview/outputs/periods",
        "steps/overview/rules/r1",
        "steps/overview/title",
        "steps/overview/why",
    ]
    refs = set()
    for path in cells:
        source = stored.sources[path]
        assert source["kind"] == "user_confirmed"  # AI 的提议，作者接受了
        entry = w.repo.entries[uuid.UUID(source["ref"].removeprefix("record:"))]
        assert (entry.kind, entry.path, entry.suggestion_ref) == (
            "accept",
            path,
            f"{done.id}:s2",
        )
        refs.add(source["ref"])
    assert len(refs) == len(cells)


@pytest.mark.asyncio
async def test_unverified_only_edited_then_accepted_and_reject(w) -> None:
    request, done = await _first_round(w)
    rid, iid = str(request.id), str(done.id)
    with pytest.raises(ConcurrencyConflictError) as caught:
        await w.inc.decide(OWNER, rid, iid, "s3", action="accept", revision=0)
    assert caught.value.code == "suggestion_unverified"
    with pytest.raises(BadRequestError):
        await w.inc.decide(OWNER, rid, iid, "s3", action="accept_edited", revision=0)
    stored, record = await w.inc.decide(
        OWNER,
        rid,
        iid,
        "s3",
        action="accept_edited",
        revision=0,
        value="按我自己的口径分析财报",
    )
    assert stored is not None
    assert stored.body["tagline"] == "按我自己的口径分析财报"
    source = stored.sources["tagline"]
    entry = w.repo.entries[uuid.UUID(source["ref"].removeprefix("record:"))]
    assert source["kind"] == "user_text"
    assert (entry.kind, entry.text, entry.suggestion_ref) == (
        "edit",
        "按我自己的口径分析财报",
        None,
    )
    assert record.decisions["s3"]["state"] == "accepted_edited"
    # 不要：不碰工作稿
    untouched, record = await w.inc.decide(OWNER, rid, iid, "s1", action="reject")
    assert untouched is None and record.decisions["s1"]["state"] == "rejected"
    assert w.repo.workdrafts[request.id].body["scope"]["does"] == ""


@pytest.mark.asyncio
async def test_a_stale_revision_is_a_conflict(w: Wired) -> None:
    request, done = await _first_round(w)
    await w.works.save(OWNER, str(request.id), revision=0, edits=[("name", "我的")])
    with pytest.raises(ConcurrencyConflictError) as caught:
        await w.inc.decide(
            OWNER, str(request.id), str(done.id), "s1", action="accept", revision=0
        )
    assert caught.value.code == "workdraft_revision"
    assert "s1" not in w.repo.incubations[done.id].decisions
    with pytest.raises(NotFoundError):
        await w.inc.decide(
            OWNER, str(request.id), str(done.id), "nope", action="reject"
        )
    with pytest.raises(NotFoundError):
        await w.inc.decide(
            "someone-else", str(request.id), str(done.id), "s1", action="reject"
        )


@pytest.mark.asyncio
async def test_answer_sources_cite_the_gap_and_publish_checks_new_refs(w) -> None:
    request, done = await _first_round(w)
    rid = str(request.id)
    _, record = await w.inc.decide(
        OWNER, rid, str(done.id), "s1", action="accept", revision=0
    )
    stored, _ = await w.inc.decide(
        OWNER, rid, str(done.id), "s2", action="accept", revision=1
    )
    assert stored is not None
    (gap,) = await w.inc.gaps(done)
    await w.questions.answer_as_user(OWNER, str(gap.id), AnswerIn(option_id="proposal"))
    # 第二轮：带上答过的缺口，答复是「按提议」
    second = await w.inc.start(
        OWNER, rid, action="revise", instruction="把毛利率口径写进第 1 步"
    )
    sent = w.incubator.started[-1]
    assert [a.model_dump() for a in sent.answers] == [
        {
            "id": str(gap.id),
            "question": "毛利率按哪个口径？",
            "proposal": "营业收入减营业成本",
            "answer": "按提议",
        }
    ]
    assert second.round == 2 and second.answer_ids == [str(gap.id)]
    w.hand_back(
        second,
        {
            "sources": [
                {
                    "path": "steps/overview/rules/r2",
                    "kind": "answer",
                    "ref": str(gap.id),
                },
                {
                    "path": "steps/overview/rules/r3",
                    "kind": "answer",
                    "ref": "q-unknown",
                },
            ],
            "suggestions": [
                {
                    "id": "s1",
                    "path": "steps/overview/rules/r2",
                    "op": "add",
                    "value": {"text": "毛利率按营业收入减营业成本"},
                    "reason": "你答了按提议",
                },
                {
                    "id": "s2",
                    "path": "steps/overview/rules/r3",
                    "op": "add",
                    "value": {"text": "没答过的"},
                    "reason": "引了一个没答过的",
                },
            ],
        },
    )
    second = await w.inc.refresh(OWNER, rid, str(second.id))
    assert (second.review or {})["s2"]["problems"] == [
        {"path": "steps/overview/rules/r3", "problem": "answer_not_found"}
    ]
    stored, _ = await w.inc.decide(
        OWNER, rid, str(second.id), "s1", action="accept", revision=stored.revision
    )
    assert stored is not None
    assert stored.sources["steps/overview/rules/r2"] == {
        "kind": "user_confirmed",
        "ref": f"question:{gap.id}",
    }
    # 出一版：创意原句、答过的缺口、接受记录都核得上
    _, problems, _ = await w.works.preview(OWNER, rid)
    assert [p for p in problems if p.path.startswith(("scope", "steps"))] == []
    draft = await w.works.publish(OWNER, rid)
    assert draft.mode == "self"
    rule_refs = {s["ref"] for s in draft.manifest["rule_sources"]}
    assert f"question:{gap.id}" in rule_refs


@pytest.mark.asyncio
async def test_publish_refuses_a_tampered_idea_ref(w: Wired) -> None:
    request, done = await _first_round(w)
    stored, _ = await w.inc.decide(
        OWNER, str(request.id), str(done.id), "s1", action="accept", revision=0
    )
    assert stored is not None
    good = stored.sources["scope/does"]["ref"]
    bad = good[:-1] + ("0" if good[-1] != "0" else "1")
    wd = w.repo.workdrafts[request.id]
    w.repo.workdrafts[request.id] = replace(
        wd, sources=wd.sources | {"scope/does": {"kind": "user_text", "ref": bad}}
    )
    _, problems, _ = await w.works.preview(OWNER, str(request.id))
    assert any(
        p.path == "scope/does" and "not in that version" in p.message for p in problems
    )


@pytest.mark.asyncio
async def test_cancel_and_listing(w: Wired) -> None:
    request = await w.request()
    record = await w.inc.start(OWNER, str(request.id), action="draft")
    cancelled = await w.inc.cancel(OWNER, str(request.id), str(record.id))
    assert cancelled.status == "cancelled" and cancelled.finished_at is not None
    assert w.incubator.cancelled == [record.run_id]
    # 结束了的再取消照原样，不再问运行方
    await w.inc.cancel(OWNER, str(request.id), str(record.id))
    assert w.incubator.cancelled == [record.run_id]
    with pytest.raises(ConcurrencyConflictError):
        await w.inc.decide(
            OWNER, str(request.id), str(record.id), "s1", action="reject"
        )
    rounds = await w.inc.rounds(OWNER, str(request.id))
    assert [(r.round, r.status) for r in rounds] == [(1, "cancelled")]
    with pytest.raises(NotFoundError):
        await w.inc.rounds("someone-else", str(request.id))
    other = await w.request()
    with pytest.raises(NotFoundError):  # 别的名称的一轮
        await w.inc.mine(OWNER, str(other.id), str(record.id))


@pytest.mark.asyncio
async def test_over_budget_and_waiting_are_kept(w: Wired) -> None:
    request = await w.request()
    record = await w.inc.start(OWNER, str(request.id), action="draft")
    w.incubator.results[record.run_id] = IncubationResultV1.model_validate(
        {
            "contract": "knowbot-incubation/v1",
            "incubation_id": record.run_id,
            "status": "waiting",
            "waiting_for": "computer_offline",
        }
    )
    waiting = await w.inc.refresh(OWNER, str(request.id), str(record.id))
    assert (waiting.status, waiting.waiting_for) == ("waiting", "computer_offline")
    w.incubator.results[record.run_id] = IncubationResultV1.model_validate(
        {
            "contract": "knowbot-incubation/v1",
            "incubation_id": record.run_id,
            "status": "over_budget",
            "cost": {"spent": "20"},
        }
    )
    over = await w.inc.refresh(OWNER, str(request.id), str(record.id))
    assert (over.status, over.spent, over.waiting_for) == ("over_budget", "20", None)


# ---------- 创意种类「问题」、导入 skill 当种子 ----------
@pytest.mark.asyncio
async def test_a_question_idea_and_a_skill_seed(w: Wired) -> None:
    with pytest.raises(ValueError):
        KnowbotRequestIn.model_validate(request_body(idea={"kind": "question"}))
    with pytest.raises(ValueError):
        KnowbotRequestIn.model_validate(
            request_body(idea={"kind": "question", "question": "x", "skill": "y"})
        )
    request = await w.request(idea={"kind": "question", "question": "收入会怎样？"})
    assert request.idea == {"kind": "question", "question": "收入会怎样？"}
    version = await w.works.seed_skill(OWNER, str(request.id), "# skill\n先看表")
    assert version == 2
    found = w.repo.versions[(request.id, 2)]
    assert found.idea == {"kind": "skill", "skill": "# skill\n先看表"}
    assert found.cases == w.repo.versions[(request.id, 1)].cases
    with pytest.raises(NotFoundError):
        await w.works.seed_skill("someone-else", str(request.id), "x")
    with pytest.raises(BadRequestError):
        await w.works.seed_skill(OWNER, str(request.id), "  ")
    # 下一轮起草用的是新的这一版
    await w.inc.start(OWNER, str(request.id), action="draft")
    sent = w.incubator.started[-1]
    assert (sent.seed.kind, sent.seed.text) == ("skill", "# skill\n先看表")
