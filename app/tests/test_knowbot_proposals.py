"""平台孵化时运营的提议与「出一版」（专家孵化方案第十二节；decisions 10-29
「孵化第 5 步的三件」）：孵化授权生效了运营才看得到工作稿、能提议；作者接受了才
进稿、出处照旧只认作者；「我自己来」时没处理的提议作废；运营出一版记平台，最新一版
创意要正式受理过；运营发起的 AI 一轮在作者的账号下、按作者设的上限跑。"""

from __future__ import annotations

from typing import Any

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_platform import KnowbotPlatformService
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.lifecycle import RevisionIn
from tests.knowbot_fakes import (
    OWNER,
    FakeIncubator,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
    own_computer,
    request_body,
)

STEP = {
    "id": "overview",
    "title": "数据摸底",
    "does": "看清数据有哪些表、哪些报告期。",
    "why": "先知道手上有什么。",
    "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
    "outputs": [{"name": "periods", "type": "list"}],
    "checks": [{"template": "list_min", "params": {"path": "periods"}}],
}
CELLS: list[tuple[str, Any]] = [
    ("scope/does", "A 股非金融公司的财务分析"),
    ("steps/overview", STEP),
]
RULE = "steps/overview/rules/r1"


@pytest.fixture
def world():
    repo, runtime, incubator = MemoryKnowlab(), FakeRuntime(), FakeIncubator()
    own_computer(repo)
    return (
        repo,
        KnowbotCreationService(repo, runtime),
        KnowbotWorkdraftService(repo),
        KnowbotProposalService(repo),
        KnowbotPlatformService(repo),
        KnowbotIncubationService(repo, incubator, runtime),
        incubator,
    )


async def _platform(svc: KnowbotCreationService):
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    return request


async def test_operators_propose_only_after_acceptance_and_authors_decide(
    world,
) -> None:
    repo, svc, works, props, *_ = world
    request = await _platform(svc)
    rid = str(request.id)
    with pytest.raises(NotFoundError):  # 还没受理：运营还不能动工作稿
        await props.operator_view(rid)
    await svc.accept(rid, 1, all_clear(), "admin-1")
    await works.save(OWNER, rid, revision=0, edits=CELLS)
    _, workdraft, none = await props.operator_view(rid)
    assert workdraft is not None and list(none) == []

    with pytest.raises(BadRequestError) as raised:  # 套不上现稿的当场拒
        await props.propose(
            rid,
            "admin-1",
            [{"path": "steps/nowhere/does", "op": "set", "value": "x", "reason": "r"}],
        )
    assert raised.value.code == "proposal_not_applicable"
    [rule, why] = await props.propose(
        rid,
        "admin-1",
        [
            {
                "path": RULE,
                "op": "set",
                "value": {"id": "r1", "text": "合并优先。"},
                "reason": "口径要定",
            },
            {
                "path": "steps/overview/why",
                "op": "set",
                "value": "心里有数。",
                "reason": "更短",
            },
        ],
    )
    # 提议不改工作稿
    _, still = await works.get(OWNER, rid)
    assert still is not None and still.revision == 1

    # 作者原样接受：记作者的接受记录，出处 user_confirmed
    stored, decided = await props.decide(
        OWNER, rid, str(rule.id), action="accept", revision=1
    )
    assert decided.state == "accepted" and stored is not None
    assert stored.body["steps"][0]["rules"][0]["text"] == "合并优先。"
    source = stored.sources["steps/overview/rules/r1"]
    assert source["kind"] == "user_confirmed"
    [entry] = [e for e in repo.entries.values() if f"record:{e.id}" == source["ref"]]
    assert (entry.kind, entry.actor_role, entry.suggestion_ref) == (
        "accept",
        "author",
        f"proposal:{rule.id}",
    )
    with pytest.raises(ConcurrencyConflictError):  # 处理过的不能再处理
        await props.decide(OWNER, rid, str(rule.id), action="reject")
    # 改了再接受：改后的字是作者自己的
    stored, decided = await props.decide(
        OWNER,
        rid,
        str(why.id),
        action="accept_edited",
        revision=stored.revision,
        value="先摸清家底。",
    )
    assert decided.state == "accepted_edited"
    assert stored.sources["steps/overview/why"]["kind"] == "user_text"
    # 别人的名称：与没有的一样
    with pytest.raises(NotFoundError):
        await props.decide("someone-else", rid, str(why.id), action="reject")


async def test_take_back_voids_what_is_pending_and_operators_withdraw(world) -> None:
    repo, svc, works, props, platform, *_ = world
    request = await _platform(svc)
    rid = str(request.id)
    await svc.accept(rid, 1, all_clear(), "admin-1")
    await works.save(OWNER, rid, revision=0, edits=CELLS)
    [first, second] = await props.propose(
        rid,
        "admin-1",
        [
            {
                "path": RULE,
                "op": "set",
                "value": {"id": "r1", "text": "a"},
                "reason": "r",
            },
            {"path": "steps/overview/why", "op": "set", "value": "b", "reason": "r"},
        ],
    )
    await props.withdraw(rid, str(first.id))
    with pytest.raises(ConcurrencyConflictError):
        await props.withdraw(rid, str(first.id))
    await platform.take_back(OWNER, rid)
    assert repo.proposals[first.id].state == "withdrawn"
    assert repo.proposals[second.id].state == "void"
    with pytest.raises(NotFoundError):  # 收回来了：运营看不到
        await props.operator_view(rid)


async def test_operators_publish_a_platform_draft_from_an_accepted_version(
    world,
) -> None:
    repo, svc, works, props, *_ = world
    request = await _platform(svc)
    rid = str(request.id)
    await svc.accept(rid, 1, all_clear(), "admin-1")
    await works.save(OWNER, rid, revision=0, edits=CELLS)
    manifest, problems, version = await works.operator_preview(rid)
    assert problems == [] and version == "1"
    draft = await works.operator_publish(rid)
    assert (draft.mode, draft.version, draft.request_version) == ("platform", "1", 1)
    assert await svc.operator_draft(str(draft.id)) == draft
    # 作者改了创意出 v2，还没受理：不能出
    body = request_body()
    await svc.revise(
        OWNER,
        rid,
        RevisionIn.model_validate(
            {k: body[k] for k in ("idea", "cases", "consent_test_data")}
        ),
    )
    with pytest.raises(ConcurrencyConflictError) as raised:
        await works.operator_publish(rid)
    assert raised.value.code == "version_not_accepted"
    # 出处对不上的照样出不了（运营写不进格子，作者没写的格子没有出处）
    await svc.accept(rid, 2, all_clear(), "admin-1")
    await props.propose(
        rid,
        "admin-1",
        [
            {
                "path": "steps/overview/rules",
                "op": "add",
                "value": {"id": "r2", "text": "只看 A 股。"},
                "reason": "范围",
            }
        ],
    )
    assert len(repo.drafts) == 1
    again = await works.operator_publish(rid)  # 没接受的提议不进稿，照旧能出
    assert again.revision == 2


async def test_operators_start_a_round_on_the_authors_account(world) -> None:
    repo, svc, works, props, platform, incubations, incubator = world
    request = await _platform(svc)
    rid = str(request.id)
    with pytest.raises(NotFoundError):
        await incubations.operator_start(rid, action="draft")
    await svc.accept(rid, 1, all_clear(), "admin-1")
    record = await incubations.operator_start(rid, action="draft")
    assert (record.mode, record.owner_subject, record.budget_limit) == (
        "platform",
        OWNER,
        "20.00",
    )
    [sent] = incubator.started
    assert sent.owner_subject == OWNER and sent.budget.limit == "20.00"
    assert [r.id for r in await incubations.operator_rounds(rid)] == [record.id]


async def test_operator_propose_remove_step_with_none_value(world) -> None:
    repo, svc, works, props, *_ = world
    request = await _platform(svc)
    rid = str(request.id)
    await svc.accept(rid, 1, all_clear(), "admin-1")
    await works.save(OWNER, rid, revision=0, edits=CELLS)
    [removed] = await props.propose(
        rid,
        "admin-1",
        [{"path": "steps/overview", "op": "remove", "value": None, "reason": "去掉"}],
    )
    assert removed.op == "remove"
    assert removed.value is None
    stored, decided = await props.decide(
        OWNER, rid, str(removed.id), action="accept", revision=1
    )
    assert decided.state == "accepted"
    assert stored is not None and stored.body.get("steps") == []
