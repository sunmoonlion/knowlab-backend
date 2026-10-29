"""工作稿的接口（web端，作者本人）：会话与 CSRF、不是自己的 404、revision 冲突 409、
存格子、预览、出一版、导入、复制。"""

from __future__ import annotations

import uuid

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
    accepted_request,
    manifest,
    platform_draft,
    request_body,
)
from tests.test_auth_routes_security import FakeAuthService, session

CSRF = "csrf-token-with-at-least-thirty-two-characters"
WEB = {"Origin": "http://localhost:3000", "X-CSRF-Token": CSRF}
WEB_SID = "sunmoonai_knowlab_web_sid"
ADMIN_SID = "sunmoonai_knowlab_admin_sid"
CELLS = [
    {"path": "scope/does", "value": "A 股非金融公司的财务分析"},
    {
        "path": "steps/overview",
        "value": {
            "title": "数据摸底",
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
async def test_the_workdraft_needs_my_web_session_and_csrf(wired) -> None:
    svc, _ = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        assert (await c.get(base)).status_code == 401
        c.cookies.set(ADMIN_SID, "admin")  # 管理端会话不算
        assert (await c.get(base)).status_code == 401
        c.cookies.set(WEB_SID, "member")
        empty = await c.get(base)
        assert empty.status_code == 200
        assert empty.headers["cache-control"] == "no-store"
        assert (empty.json()["revision"], empty.json()["sources"]) == (0, {})
        assert empty.json()["origins"] == {}
        assert empty.json()["body"]["steps"] == []
        body = {"revision": 0, "cells": CELLS}
        assert (await c.post(f"{base}/cells", json=body)).status_code == 403
        for tail, payload in (
            ("publish", None),
            ("import", {"revision": 0, "manifest": manifest()}),
            ("copy", {"revision": 0, "draft_id": str(uuid.uuid4())}),
        ):
            assert (await c.post(f"{base}/{tail}", json=payload)).status_code == 403


@pytest.mark.asyncio
async def test_someone_elses_workdraft_is_not_found(wired) -> None:
    svc, _ = wired
    theirs = await _mine(svc, "someone-else")
    base = f"/api/web/v1/knowbot-requests/{theirs.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        assert (await c.get(base)).status_code == 404
        assert (await c.get(f"{base}/preview")).status_code == 404
        saved = await c.post(
            f"{base}/cells", json={"revision": 0, "cells": CELLS}, headers=WEB
        )
        assert saved.status_code == 404
        assert saved.json()["code"] == "request_not_found"
        assert (await c.post(f"{base}/publish", headers=WEB)).status_code == 404
        assert (
            await c.get("/api/web/v1/knowbot-requests/x/workdraft")
        ).status_code == (404)


@pytest.mark.asyncio
async def test_save_preview_publish_and_a_stale_revision(wired) -> None:
    svc, repo = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        saved = await c.post(
            f"{base}/cells", json={"revision": 0, "cells": CELLS}, headers=WEB
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert saved.json()["sources"]["steps/overview/rules/r1"]["kind"] == "user_text"
        # 每格旁边标它是怎么来的：在编辑器里写的
        assert saved.json()["origins"]["steps/overview/rules/r1"] == "edit"
        assert (await c.get(base)).json()["origins"]["scope/does"] == "edit"
        stale = await c.post(
            f"{base}/cells",
            json={"revision": 0, "cells": [{"path": "name", "value": "x"}]},
            headers=WEB,
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "workdraft_revision"
        bad = await c.post(
            f"{base}/cells",
            json={"revision": 1, "cells": [{"path": "nope", "value": "x"}]},
            headers=WEB,
        )
        assert bad.status_code == 400
        missing = await c.post(
            f"{base}/cells",
            json={"revision": 1, "cells": [{"path": "name"}]},
            headers=WEB,
        )
        assert missing.status_code in {400, 422}
        preview = await c.get(f"{base}/preview")
        assert preview.status_code == 200
        assert preview.json()["problems"] == []
        assert preview.json()["version"] == "1"
        assert preview.json()["manifest"]["schema"] == "knowbot-manifest/v2"
        assert repo.drafts == {}
        made = await c.post(f"{base}/publish", headers=WEB)
        assert made.status_code == 201
        assert made.json() | {"draft_id": ""} == {
            "contract_version": 1,
            "draft_id": "",
            "version": "1",
            "revision": 1,
            "request_version": 1,
            "mode": "self",
        }
        detail = await c.get(f"/api/web/v1/knowbot-requests/{request.id}")
        assert [d["mode"] for d in detail.json()["drafts"]] == ["self"]


@pytest.mark.asyncio
async def test_publish_names_the_cells_that_do_not_hold(wired) -> None:
    svc, _ = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        await c.post(
            f"{base}/cells",
            json={"revision": 0, "cells": [{"path": "scope/does", "value": "x"}]},
            headers=WEB,
        )
        refused = await c.post(f"{base}/publish", headers=WEB)
        assert refused.status_code == 400
        assert refused.json()["code"] == "workdraft_invalid"
        assert "steps: add at least one step" in refused.json()["detail"]
        preview = await c.get(f"{base}/preview")
        assert {"path": "steps", "message": "add at least one step"} in preview.json()[
            "problems"
        ]


@pytest.mark.asyncio
async def test_import_and_copy_my_own(wired) -> None:
    svc, repo = wired
    request = await _mine(svc)
    target = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    other = f"/api/web/v1/knowbot-requests/{target.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        await c.post(f"{base}/cells", json={"revision": 0, "cells": CELLS}, headers=WEB)
        draft = (await c.post(f"{base}/publish", headers=WEB)).json()
        imported = await c.post(
            f"{base}/import",
            json={
                "revision": 1,
                "manifest": repo.drafts[uuid.UUID(draft["draft_id"])].manifest,
            },
            headers=WEB,
        )
        assert imported.status_code == 200
        assert imported.json()["revision"] == 2 and imported.json()["notes"] == []
        assert set(imported.json()["origins"].values()) == {"import"}
        bad = await c.post(
            f"{base}/import", json={"revision": 2, "manifest": {}}, headers=WEB
        )
        assert bad.status_code == 400 and bad.json()["code"] == "manifest_invalid"
        copied = await c.post(
            f"{other}/copy",
            json={"revision": 0, "draft_id": draft["draft_id"]},
            headers=WEB,
        )
        assert copied.status_code == 200
        assert copied.json()["body"]["steps"][0]["title"] == "数据摸底"
        assert copied.json()["origins"]["steps/overview/title"] == "copy"
        theirs = await accepted_request(svc, "someone-else")
        foreign = await platform_draft(svc, str(theirs.id), manifest())
        refused = await c.post(
            f"{other}/copy",
            json={"revision": 1, "draft_id": str(foreign.id)},
            headers=WEB,
        )
        assert refused.status_code == 404
        assert refused.json()["code"] == "draft_not_found"
