"""AI 孵化一轮的接口（web端，作者本人）：会话与 CSRF、不是自己的 404、发起（默认上限与
上次的电脑）、一轮在跑 409、刷新存交回、建议三种处理与出处核不上、缺口进审核栏、导入
skill 当种子；管理端看不到缺口。"""

from __future__ import annotations

from typing import Any

import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_questions import KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.incubation import IncubationResultV1
from app.interfaces.endpoints import knowbot_routes
from app.main import app
from tests.knowbot_fakes import (
    OWNER,
    FakeIncubator,
    FakeRuntime,
    own_computer,
    request_body,
)
from tests.test_auth_routes_security import FakeAuthService, session
from tests.test_knowbot_workdraft_routes import ADMIN_SID, WEB, WEB_SID, client

HANDBACK: dict[str, Any] = {
    "sources": [
        {"path": "scope/does", "kind": "idea", "quote": "按我的口径分析一家公司"},
        {"path": "tagline", "kind": "idea", "quote": "创意里没有这句"},
    ],
    "gaps": [{"id": "g1", "question": "毛利率按哪个口径？", "proposal": "合并口径"}],
    "suggestions": [
        {
            "id": "s1",
            "path": "scope/does",
            "op": "set",
            "value": "分析一家公司",
            "reason": "创意里写了",
        },
        {
            "id": "s2",
            "path": "tagline",
            "op": "set",
            "value": "我的方法",
            "reason": "一句话介绍",
        },
    ],
}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    from tests.test_formal_method_service import Repository

    repo, runtime, incubator = Repository(), FakeRuntime(), FakeIncubator()
    own_computer(repo)
    svc = KnowbotCreationService(repo, runtime)
    web = FakeAuthService({"member": session("web", "profile:read")})
    admin = FakeAuthService(
        {"admin": session("admin", "knowlab:admin", "knowlab:release")}
    )
    monkeypatch.setattr(auth_middleware, "web_auth_service", web)
    monkeypatch.setattr(auth_middleware, "admin_auth_service", admin)
    app.dependency_overrides[knowbot_routes.service] = lambda: svc
    app.dependency_overrides[knowbot_routes.workdraft_service] = lambda: (
        KnowbotWorkdraftService(repo)
    )
    app.dependency_overrides[knowbot_routes.incubation_service] = lambda: (
        KnowbotIncubationService(repo, incubator, runtime)
    )
    app.dependency_overrides[knowbot_routes.board_service] = lambda: KnowbotBoard(repo)
    app.dependency_overrides[knowbot_routes.proposal_service] = lambda: (
        KnowbotProposalService(repo)
    )
    app.dependency_overrides[knowbot_routes.question_service] = lambda: (
        KnowbotQuestionService(repo, runtime)
    )
    yield svc, repo, incubator
    app.dependency_overrides.clear()


async def _mine(svc: KnowbotCreationService, owner: str = OWNER):
    return await svc.submit(owner, KnowbotRequestIn.model_validate(request_body()))


@pytest.mark.asyncio
async def test_rounds_need_my_session_csrf_and_my_name(wired) -> None:
    svc, _, incubator = wired
    mine, theirs = await _mine(svc), await _mine(svc, "someone-else")
    base = f"/api/web/v1/knowbot-requests/{mine.id}/workdraft/incubations"
    async with client() as c:
        assert (await c.get(base)).status_code == 401
        c.cookies.set(ADMIN_SID, "admin")
        assert (await c.post(base, json={"action": "draft"})).status_code == 401
        c.cookies.set(WEB_SID, "member")
        assert (await c.post(base, json={"action": "draft"})).status_code == 403
        other = f"/api/web/v1/knowbot-requests/{theirs.id}/workdraft/incubations"
        assert (await c.get(other)).status_code == 404
        refused = await c.post(other, json={"action": "draft"}, headers=WEB)
        assert refused.status_code == 404
        assert (
            await c.post(
                base, json={"action": "draft", "selection_revision": 1}, headers=WEB
            )
        ).status_code == 404
    assert incubator.started == []


@pytest.mark.asyncio
async def test_method_directory_uses_logged_in_owner_before_and_during_incubation(
    wired, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.application.services.formal_methods import FormalMethodService

    svc, repo, _ = wired
    request = await _mine(svc)
    foreign = await _mine(svc, "another-owner")
    sources = AsyncMock(return_value={"items": []})
    tools = AsyncMock(return_value={"items": [], "next": None})
    monkeypatch.setattr(svc.runtimes, "formal_method_sources", sources, raising=False)
    monkeypatch.setattr(svc.runtimes, "incubation_tool_versions", tools, raising=False)
    app.dependency_overrides[knowbot_routes.formal_service] = lambda: (
        FormalMethodService(repo, svc.runtimes)
    )
    async with client() as c:
        home = "/api/web/v1/method-directory"
        assert (await c.get(home + "/sources")).status_code == 401
        c.cookies.set(WEB_SID, "member")
        draft = f"/api/web/v1/knowbot-requests/{request.id}"
        for url in (home + "/sources", draft + "/formal-method-sources"):
            assert (await c.get(url)).status_code == 200
            sources.assert_awaited_with("knowbench", OWNER)
        for url in (home + "/shared-tools", draft + "/incubation-tools"):
            assert (await c.get(url)).status_code == 200
            tools.assert_awaited_with(
                "knowbench", OWNER, reference=None, after=None, declaration=None
            )
        tools.reset_mock()
        retired = f"{draft}/incubation-tools/{request.id}/versions/1"
        assert (
            await c.get(retired, params={"digest": "sha256:" + "a" * 64})
        ).status_code == 404
        tools.assert_not_awaited()
        assert (
            await c.get(
                f"/api/web/v1/knowbot-requests/{foreign.id}/formal-method-sources"
            )
        ).status_code == 404
        browse = AsyncMock(
            return_value={"items": [], "categories": [], "total": 0, "next": None}
        )
        monkeypatch.setattr(svc.runtimes, "browse_directory", browse, raising=False)
        url = home + "/browse/owned?kind=tool&category=data.lookup&request_id="
        assert (await c.get(url + str(foreign.id))).status_code == 404
        browse.assert_not_awaited()
        assert (await c.get(url + str(request.id))).status_code == 200
        browse.assert_awaited_once_with(
            "knowbench",
            OWNER,
            "owned",
            {"kind": "tool", "offset": 0, "category": "data.lookup"},
        )
        assert sources.await_count == 2


@pytest.mark.asyncio
async def test_author_reviews_selection_before_saving_and_source_is_rechecked(
    wired, monkeypatch
):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    svc, repo, incubator = wired
    request = await _mine(svc)
    from app.application.services.method_drafts import HandoffSeedIn, MethodDraftService
    from app.domain.knowbots.formal_method import MethodDraftV2

    mid = uuid4()
    await MethodDraftService(repo).create_from_handoff(
        OWNER,
        HandoffSeedIn(
            title="核对",
            goal="核对",
            draft_contract=MethodDraftV2(
                title="核对",
                purpose="核对",
                method_kind="tool",
                applies_when=["年度数据齐全"],
                excludes=["季度预测"],
            ),
        ),
        method_id=mid,
        request_id=request.id,
    )
    source = dict(
        asset_id=None,
        asset_revision=None,
        kind="registered_instruction",
        source=str(uuid4()),
        entry="核对四行",
        version="1",
        digest="sha256:" + "a" * 64,
        license_basis="author_owned",
    )
    sources = AsyncMock(return_value={"items": [source]})
    for name, fn in {
        "method_skill_center_incubation_methods": AsyncMock(return_value=[]),
        "incubation_tool_versions": AsyncMock(return_value={"items": [], "next": None}),
        "formal_method_sources": sources,
        "planning_capabilities": AsyncMock(
            return_value={"patterns": {"sequence": True}}
        ),
    }.items():
        monkeypatch.setattr(svc.runtimes, name, fn, raising=False)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        r = await c.post(
            base + "/incubations",
            json={"action": "draft", "selection_revision": 1},
            headers=WEB,
        )
        assert r.status_code == 201, r.text
        view = r.json()["incubation"]
        url = base + "/incubations/" + view["incubation_id"]
        record = next(iter(repo.incubations.values()))
        assert (
            record.seed["selection_context"]["instructions"]["i1"]["digest"]
            == source["digest"]
        )
        selection = dict(
            status="ready",
            reason="已获权的核对指令足够",
            steps=[dict(id="check", task="核对数据", method="i1")],
            patterns=[],
            routing_required=False,
        )
        incubator.results[record.run_id] = IncubationResultV1.model_validate(
            dict(
                contract="knowbot-incubation/v1",
                incubation_id=record.run_id,
                status="succeeded",
                cost={"spent": "0.12"},
                handback={"selection": selection},
            )
        )
        refresh = await c.post(url + "/refresh", headers=WEB)
        assert refresh.json()["incubation"]["selection"]["status"] == "ready"
        assert (await MethodDraftService(repo).get(OWNER, str(mid)))[
            "formal_method"
        ] is None
        command = dict(
            revision=1,
            method_id=str(mid),
            cases={},
            template=dict(
                template="checked_delivery/v1",
                goal="核对",
                deliverable="四行小计",
                check="逐行一致",
                evidence="原表",
            ),
            step_text={"check": "逐行核对后留依据"},
            confirmed=True,
        )
        assert (
            await c.post(
                url + "/confirm-selection",
                headers=WEB,
                json={**command, "confirmed": False},
            )
        ).status_code in (400, 422)
        sources.return_value = {"items": []}
        assert (
            await c.post(url + "/confirm-selection", headers=WEB, json=command)
        ).status_code == 404
        sources.return_value = {"items": [source]}
        saved = await c.post(url + "/confirm-selection", headers=WEB, json=command)
        assert saved.status_code == 200, saved.text
        result = saved.json()
        detail = await MethodDraftService(repo).get(OWNER, str(mid))
        assert detail["draft_contract"]["applies_when"] == ["年度数据齐全"]
        assert detail["draft_contract"]["excludes"] == ["季度预测"]
        assert detail["source"] == source
        assert detail["draft_contract"]["procedure"]["instructions"].startswith(
            "逐行核对后留依据"
        )
        assert detail["formal_method"] is None
        replay = await c.post(url + "/confirm-selection", headers=WEB, json=command)
        assert replay.json()["revision"] == result["revision"]
        assert (
            await c.post(
                url + "/confirm-selection",
                headers=WEB,
                json={**command, "step_text": {"check": "覆盖"}},
            )
        ).status_code == 409


@pytest.mark.asyncio
async def test_archived_workdraft_cannot_invoke_retired_manual_tools(wired):
    request = await _mine(wired[0])
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        vocab = await c.get("/api/web/v1/slot-vocabulary")
        assert vocab.status_code == 200 and all(
            isinstance(domain["label"], str) for domain in vocab.json()["domains"]
        )
        base = f"/api/web/v1/knowbot-requests/{request.id}"
        for path in (
            "workdraft/column-check",
            "workdraft/candidate-preview",
            "incubation-tools/matching-outputs",
            "incubation-tools/scan-outputs",
        ):
            assert (
                await c.post(f"{base}/{path}", json={}, headers=WEB)
            ).status_code == 404
