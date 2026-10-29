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
    COMPUTER,
    OWNER,
    FakeIncubator,
    FakeRuntime,
    MemoryKnowlab,
    own_computer,
    request_body,
)
from tests.test_auth_routes_security import FakeAuthService, session
from tests.test_knowbot_creation_routes import ADMIN
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
    repo, runtime, incubator = MemoryKnowlab(), FakeRuntime(), FakeIncubator()
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
        assert (await c.post(base, json={"action": "x"}, headers=WEB)).status_code in {
            400,
            422,
        }
    assert incubator.started == []


@pytest.mark.asyncio
async def test_start_refresh_decide_and_gaps_in_the_inbox(wired) -> None:
    svc, repo, incubator = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        computers = (await c.get(f"{base}/computers")).json()
        assert computers["last"]["environment_id"] == COMPUTER
        started = await c.post(
            f"{base}/incubations", json={"action": "draft"}, headers=WEB
        )
        assert started.status_code == 201
        view = started.json()["incubation"]
        assert (view["round"], view["status"], view["budget_limit"]) == (
            1,
            "queued",
            "20",
        )
        assert view["environment_id"] == COMPUTER
        assert view["suggestions"] == [] and view["gaps"] == []
        again = await c.post(
            f"{base}/incubations", json={"action": "draft"}, headers=WEB
        )
        assert again.status_code == 409
        assert again.json()["code"] == "incubation_running"
        iid = view["incubation_id"]
        run = repo.incubations[next(iter(repo.incubations))].run_id
        incubator.results[run] = IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": run,
                "status": "succeeded",
                "cost": {"spent": "2.4"},
                "handback": HANDBACK,
            }
        )
        refreshed = await c.post(f"{base}/incubations/{iid}/refresh", headers=WEB)
        assert refreshed.status_code == 200
        view = refreshed.json()["incubation"]
        assert (view["status"], view["spent"]) == ("succeeded", "2.4")
        s1, s2 = view["suggestions"]
        assert (s1["verified"], s1["kinds"], s1["state"]) == (True, ["idea"], "pending")
        assert s1["sources"] == [HANDBACK["sources"][0]]
        assert s2["verified"] is False
        assert s2["problems"] == [{"path": "tagline", "problem": "quote_not_in_idea"}]
        (gap,) = view["gaps"]
        assert (gap["question"], gap["proposal"], gap["answered"]) == (
            "毛利率按哪个口径？",
            "合并口径",
            False,
        )
        listed = (await c.get(f"{base}/incubations")).json()["incubations"]
        assert [(r["round"], r["status"]) for r in listed] == [(1, "succeeded")]
        assert "suggestions" not in listed[0]

        # 出处核不上的：原样接受 409，改了再接受行
        unverified = await c.post(
            f"{base}/incubations/{iid}/suggestions/s2",
            json={"action": "accept", "revision": 0},
            headers=WEB,
        )
        assert unverified.status_code == 409
        assert unverified.json()["code"] == "suggestion_unverified"
        accepted = await c.post(
            f"{base}/incubations/{iid}/suggestions/s1",
            json={"action": "accept", "revision": 0},
            headers=WEB,
        )
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["workdraft"]["body"]["scope"]["does"] == "分析一家公司"
        assert body["workdraft"]["origins"]["scope/does"] == "idea"
        assert body["incubation"]["suggestions"][0]["state"] == "accepted"
        stale = await c.post(
            f"{base}/incubations/{iid}/suggestions/s2",
            json={"action": "accept_edited", "revision": 0, "value": "我的"},
            headers=WEB,
        )
        assert stale.status_code == 409 and stale.json()["code"] == "workdraft_revision"
        edited = await c.post(
            f"{base}/incubations/{iid}/suggestions/s2",
            json={"action": "accept_edited", "revision": 1, "value": "我自己的方法"},
            headers=WEB,
        )
        assert edited.status_code == 200
        assert edited.json()["workdraft"]["origins"]["tagline"] == "edit"

        # 缺口在审核栏里（待办），作者答「按提议」
        inbox = (await c.get("/api/web/v1/knowbot-board/inbox")).json()
        (item,) = [i for i in inbox["items"] if i["type"] == "question"]
        assert (item["kind"], item["source"], item["draft_id"], item["label"]) == (
            "gap",
            "incubation",
            None,
            None,
        )
        assert item["proposal"] == "合并口径" and item["incubation_id"] == iid
        assert (await c.get("/api/web/v1/knowbot-counts")).json()["inbox"] == 1
        answered = await c.post(
            f"/api/web/v1/knowbot-questions/{item['question_id']}/answer",
            json={"option_id": "proposal"},
            headers=WEB,
        )
        assert answered.status_code == 200
        view = (await c.get(f"{base}/incubations/{iid}")).json()["incubation"]
        assert view["gaps"][0]["answered"] is True
        inbox = (await c.get("/api/web/v1/knowbot-board/inbox")).json()
        assert [a["source"] for a in inbox["answered"]] == ["incubation"]

        # 运营看不到缺口
        c.cookies.set(ADMIN_SID, "admin")
        queue = (await c.get("/api/admin/v1/knowbot-questions")).json()
        assert queue["questions"] == []
        refused = await c.post(
            f"/api/admin/v1/knowbot-questions/{item['question_id']}/answer",
            json={"text": "x"},
            headers=ADMIN,
        )
        assert refused.status_code == 404
        detail = (await c.get(f"/api/admin/v1/knowbot-requests/{request.id}")).json()
        assert detail["answered"] == []


@pytest.mark.asyncio
async def test_cancel_and_seed_a_skill(wired) -> None:
    svc, _, incubator = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        seeded = await c.post(
            f"{base}/seed-skill", json={"skill": "# 我的 skill"}, headers=WEB
        )
        assert seeded.status_code == 201 and seeded.json()["version"] == 2
        started = await c.post(
            f"{base}/incubations",
            json={"action": "draft", "budget_limit": "8"},
            headers=WEB,
        )
        iid = started.json()["incubation"]["incubation_id"]
        assert incubator.started[0].seed.kind == "skill"
        assert incubator.started[0].budget.limit == "8"
        cancelled = await c.post(f"{base}/incubations/{iid}/cancel", headers=WEB)
        assert cancelled.json()["incubation"]["status"] == "cancelled"
        empty = await c.post(f"{base}/seed-skill", json={"skill": ""}, headers=WEB)
        assert empty.status_code in {400, 422}
