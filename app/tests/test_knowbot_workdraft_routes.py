"""工作稿的接口（web端，作者本人）：会话与 CSRF、不是自己的 404、revision 冲突 409、
存格子、预览、出一版、导入、复制。"""

from __future__ import annotations

import httpx
import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.interfaces.endpoints import knowbot_routes
from app.main import app
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryKnowlab,
    request_body,
    single_method_planning,
)
from tests.test_auth_routes_security import FakeAuthService, session

CSRF = "csrf-token-with-at-least-thirty-two-characters"
WEB = {"Origin": "http://localhost:3000", "X-CSRF-Token": CSRF}
WEB_SID = "sunmoonai_knowlab_web_sid"
ADMIN_SID = "sunmoonai_knowlab_admin_sid"

CELLS = [
    {"path": "planning", "value": single_method_planning("overview")},
    {"path": "scope/does", "value": "A 股非金融公司的财务分析"},
    {
        "path": "steps/overview",
        "value": {
            "title": "数据摸底",
            "selection": {
                "applies_when": "交付该结果时",
                "required": True,
                "max_calls": 1,
            },
            "does": "看清数据有哪些表、哪些报告期。",
            "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
            "outputs": [{"name": "periods", "type": "list"}],
        },
    },
]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    repo, runtime = MemoryKnowlab(), FakeRuntime()
    svc = KnowbotCreationService(repo, runtime)
    works = KnowbotWorkdraftService(repo)
    web = FakeAuthService({"member": session("web", "profile:read")})
    admin = FakeAuthService(
        {"admin": session("admin", "knowlab:admin", "knowlab:release")}
    )
    monkeypatch.setattr(auth_middleware, "web_auth_service", web)
    monkeypatch.setattr(auth_middleware, "admin_auth_service", admin)
    app.dependency_overrides[knowbot_routes.service] = lambda: svc
    app.dependency_overrides[knowbot_routes.workdraft_service] = lambda: works
    app.dependency_overrides[knowbot_routes.proposal_service] = lambda: (
        KnowbotProposalService(repo)
    )
    app.dependency_overrides[knowbot_routes.board_service] = lambda: KnowbotBoard(repo)
    yield svc, repo
    app.dependency_overrides.clear()


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _mine(svc: KnowbotCreationService, owner: str = OWNER):
    return await svc.submit(owner, KnowbotRequestIn.model_validate(request_body()))


@pytest.mark.asyncio
async def test_retired_request_routes_are_absent_even_for_the_owner(wired):
    request = await _mine(wired[0])
    base = f"/api/web/v1/knowbot-requests/{request.id}"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        for method, path in [
            ("GET", base),
            ("GET", base + "/workdraft"),
            ("POST", base + "/versions"),
            ("POST", base + "/workdraft/cells"),
            ("POST", base + "/workdraft/import"),
            ("POST", base + "/workdraft/copy"),
            ("GET", base + "/workdraft/preview"),
            ("POST", base + "/workdraft/publish"),
            ("POST", "/api/web/v1/knowbot-requests"),
            ("GET", "/api/web/v1/knowbot-board/inbox"),
            ("GET", "/api/web/v1/knowbot-board/applications"),
        ]:
            response = await c.request(method, path, headers=WEB)
            assert response.status_code == 404, (method, path, response.text)
