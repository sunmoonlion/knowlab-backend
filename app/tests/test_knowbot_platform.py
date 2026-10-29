"""孵化与测试在自助、平台之间切换（SDD 0046 第二、四节；decisions 10-29
「孵化第 5 步的三件」）：
选模式、请平台孵化、受理、我自己来、续期、交还、交平台测与轻受理；运营看得到什么一律按
还有效的授权算。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.errors.exceptions import (
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_board import KnowbotBoard, light_items
from app.application.services.knowbot_platform import KnowbotPlatformService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.lifecycle import RevisionIn
from app.domain.knowbots.platform_grants import (
    LightAcceptanceIn,
    PlatformIncubationIn,
    PlatformTestIn,
)
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
    manifest,
    own_computer,
    request_body,
)

PLATFORM = PlatformIncubationIn(agree=True, budget_limit="20")


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 29, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def world():
    repo, clock = MemoryKnowlab(), Clock()
    own_computer(repo)
    svc = KnowbotCreationService(repo, FakeRuntime(), now=clock)
    return (
        svc,
        KnowbotPlatformService(repo, now=clock),
        KnowbotBoard(repo, now=clock),
        repo,
        clock,
    )


def _self_body(**over):
    body = request_body(mode="self", **over)
    body.pop("budget_limit")
    return KnowbotRequestIn.model_validate(body)


def _revision() -> RevisionIn:
    body = request_body()
    keys = ("idea", "cases", "consent_test_data")
    return RevisionIn.model_validate({k: body[k] for k in keys})


async def test_a_self_name_is_never_seen_by_operators(world) -> None:
    svc, _, board, repo, _ = world
    request = await svc.submit(OWNER, _self_body())
    assert request.incubation_mode == "self"
    first = repo.acceptances[(request.id, 1)]
    assert (first.kind, first.accepted) == ("self", True)  # 「自助（不受理）」
    await svc.revise(OWNER, str(request.id), _revision())
    assert repo.acceptances[(request.id, 2)].kind == "self"
    assert await board.lives(operator=True) == []
    with pytest.raises(NotFoundError):
        await board.life(str(request.id), operator=True)
    with pytest.raises(NotFoundError):  # 运营也不能受理它
        await svc.accept(str(request.id), 2, all_clear(), "admin-1")
    assert await svc.default_mode(OWNER) == "self"


async def test_asking_the_platform_needs_acceptance_then_works_for_30_days(
    world,
) -> None:
    svc, platform, board, repo, clock = world
    assert await svc.default_mode(OWNER) == ""  # 第一次没有默认
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    [grant] = await repo.list_grants([request.id])
    assert (grant.kind, grant.status, str(grant.budget_limit)) == (
        "incubate",
        "pending",
        "20",
    )
    [life] = await board.lives(operator=True)
    assert [v.version for v in life.versions] == [1]  # 待受理：看得到创意
    await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    [grant] = await repo.list_grants([request.id])
    assert grant.status == "active"
    assert grant.expires_at == clock.at + timedelta(days=30)
    assert await svc.default_mode(OWNER) == "platform"

    # 过了 30 天：运营看不到了；作者一键续
    clock.at += timedelta(days=31)
    assert await board.lives(operator=True) == []
    renewed = await platform.renew(OWNER, str(request.id))
    assert renewed.expires_at == clock.at + timedelta(days=30)
    assert len(await board.lives(operator=True)) == 1

    # 平台孵化做完交还：授权结束，名称转自助
    await platform.hand_back(str(request.id), "admin-1")
    [grant] = await repo.list_grants([request.id])
    assert (grant.status, grant.ended_by) == ("ended", "admin-1")
    assert repo.requests[request.id].incubation_mode == "self"
    assert await board.lives(operator=True) == []
    with pytest.raises(ConcurrencyConflictError):
        await platform.renew(OWNER, str(request.id))


async def test_take_back_revokes_at_once_and_hides_everything(world) -> None:
    svc, platform, board, repo, _ = world
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.revise(OWNER, str(request.id), _revision())  # v2 等受理
    assert (await board.life(str(request.id), operator=True)).drafts == (draft,)

    await platform.take_back(OWNER, str(request.id))
    [grant] = await repo.list_grants([request.id])
    assert (grant.status, grant.ended_by) == ("revoked", OWNER)
    assert repo.requests[request.id].incubation_mode == "self"
    # 最新一版没有受理单的补记「自助（不受理）」
    assert repo.acceptances[(request.id, 2)].kind == "self"
    assert await board.lives(operator=True) == []
    with pytest.raises(NotFoundError):
        await svc.operator_draft(str(draft.id))
    # 不是自己的名称：与没有的一样
    with pytest.raises(NotFoundError):
        await platform.take_back("someone-else", str(request.id))


async def test_switching_from_self_replaces_the_self_acceptance(world) -> None:
    svc, platform, board, repo, _ = world
    request = await svc.submit(OWNER, _self_body())
    grant = await platform.ask_platform(OWNER, str(request.id), PLATFORM)
    assert grant.status == "pending"
    assert repo.requests[request.id].incubation_mode == "platform"
    with pytest.raises(ConcurrencyConflictError) as raised:  # 一个名称一张
        await platform.ask_platform(OWNER, str(request.id), PLATFORM)
    assert raised.value.code == "already_platform"
    life = await board.life(str(request.id), operator=True)
    assert life.acceptances == {}  # 运营看不到自助受理单
    await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    found = repo.acceptances[(request.id, 1)]
    assert (found.kind, found.decided_by) == ("formal", "admin-1")
    [grant] = await repo.list_grants([request.id])
    assert grant.status == "active"
    # 收回来再请：这一版正式受理过，授权直接生效
    await platform.take_back(OWNER, str(request.id))
    again = await platform.ask_platform(OWNER, str(request.id), PLATFORM)
    assert again.status == "active"


async def test_a_self_draft_goes_to_the_platform_for_one_test(world) -> None:
    svc, platform, board, repo, _ = world
    request = await svc.submit(OWNER, _self_body())
    draft = await repo.create_draft(
        request_id=request.id,
        request_version=1,
        revision=1,
        version="1",
        manifest=manifest(),
        mode="self",
        rules_reviewed_by="system:workdraft",
    )
    assert await board.lives(operator=True) == []
    grant = await platform.ask_platform_test(
        OWNER, str(draft.id), PlatformTestIn(agree=True, budget_limit="8.50")
    )
    lives = await board.lives(operator=True)
    [(category, life, shown, _)] = light_items(lives)
    assert (category, shown.id) == ("light_pending", draft.id)
    # 只交了这一位：看不到创意与之前的东西
    assert life.versions == () and life.acceptances == {}
    with pytest.raises(ConcurrencyConflictError):  # 不接要说理由
        await platform.decide_test(
            str(grant.id), LightAcceptanceIn(accepted=False), "admin-1"
        )
    decided = await platform.decide_test(
        str(grant.id), LightAcceptanceIn(accepted=True), "admin-1"
    )
    assert decided.status == "active"
    assert await svc.platform_budget(await svc.operator_draft(str(draft.id))) == "8.50"
    # 还没开测：作者可以收回，收回了运营就不能发起
    await platform.withdraw_test(OWNER, str(grant.id))
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.platform_budget(draft)
    assert raised.value.code == "not_authorised"


async def test_a_declined_platform_test_is_not_shown(world) -> None:
    svc, platform, board, repo, _ = world
    request = await svc.submit(OWNER, _self_body())
    draft = await repo.create_draft(
        request_id=request.id,
        request_version=1,
        revision=1,
        version="1",
        manifest=manifest(),
        mode="self",
    )
    grant = await platform.ask_platform_test(
        OWNER, str(draft.id), PlatformTestIn(agree=True, budget_limit="5")
    )
    await platform.decide_test(
        str(grant.id), LightAcceptanceIn(accepted=False, reason="排满了"), "a"
    )
    assert await board.lives(operator=True) == []
    with pytest.raises(NotFoundError):
        await svc.operator_draft(str(draft.id))
    # 平台孵化的准专家不用交平台测
    with pytest.raises(NotFoundError):
        await platform.ask_platform_test(
            "someone-else", str(draft.id), PlatformTestIn(agree=True, budget_limit="5")
        )
