"""分身创建的接口：会话与权限、所有者隔离、CSRF、报告不带分析内容。"""

from __future__ import annotations

import httpx
import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_service import KnowbotCreationService
from app.interfaces.endpoints import knowbot_routes
from app.main import app
from tests.knowbot_fakes import (
    FakeKnowmart,
    FakeRuntime,
    MemoryDataStore,
    MemoryKnowlab,
    manifest,
    request_body,
    result,
)
from tests.test_auth_routes_security import FakeAuthService, session

CSRF = "csrf-token-with-at-least-thirty-two-characters"
WEB = {"Origin": "http://localhost:3000", "X-CSRF-Token": CSRF}
ADMIN = {"Origin": "http://localhost:5173", "X-CSRF-Token": CSRF}
WEB_SID = "sunmoonai_knowlab_web_sid"
ADMIN_SID = "sunmoonai_knowlab_admin_sid"


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    repo, runtime, knowmart = MemoryKnowlab(), FakeRuntime(), FakeKnowmart()
    svc = KnowbotCreationService(repo, runtime, knowmart)
    web = FakeAuthService({"member": session("web", "profile:read")})
    admin = FakeAuthService(
        {
            "admin": session("admin", "knowlab:admin"),
            "no-scope": session("admin", "profile:read"),
        }
    )
    monkeypatch.setattr(auth_middleware, "web_auth_service", web)
    monkeypatch.setattr(auth_middleware, "admin_auth_service", admin)
    monkeypatch.setattr(knowbot_routes, "runtime_clients", lambda: runtime)
    app.dependency_overrides[knowbot_routes.service] = lambda: svc
    data = KnowbotDataService(repo, MemoryDataStore())
    app.dependency_overrides[knowbot_routes.data_service] = lambda: data
    yield svc, repo, runtime, knowmart
    app.dependency_overrides.clear()


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_web_needs_a_session_and_csrf(wired) -> None:
    async with client() as c:
        assert (await c.get("/api/web/v1/knowbot-requests")).status_code == 401
        c.cookies.set(WEB_SID, "member")
        assert (await c.get("/api/web/v1/knowbot-runtimes")).json()["runtimes"] == [
            "investment"
        ]
        no_csrf = await c.post("/api/web/v1/knowbot-requests", json=request_body())
        assert no_csrf.status_code == 403
        bad = await c.post(
            "/api/web/v1/knowbot-requests",
            json=request_body(consent_test_data=False),
            headers=WEB,
        )
        assert bad.status_code in {400, 422}
        ok = await c.post(
            "/api/web/v1/knowbot-requests", json=request_body(), headers=WEB
        )
        assert ok.status_code == 201
        assert ok.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_admin_routes_need_the_admin_scope_and_not_a_web_session(wired) -> None:
    async with client() as c:
        assert (await c.get("/api/admin/v1/knowbot-drafts")).status_code == 401
        c.cookies.set(WEB_SID, "member")
        assert (await c.get("/api/admin/v1/knowbot-drafts")).status_code == 401
        c.cookies.set(ADMIN_SID, "no-scope")
        assert (await c.get("/api/admin/v1/knowbot-drafts")).status_code == 403
        c.cookies.set(ADMIN_SID, "admin")
        assert (await c.get("/api/admin/v1/knowbot-drafts")).status_code == 200
        bad_status = await c.get("/api/admin/v1/knowbot-drafts?status=nope")
        assert bad_status.status_code in {400, 422}


@pytest.mark.asyncio
async def test_full_flow_through_the_api(wired) -> None:
    svc, repo, runtime, knowmart = wired
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        made = await c.post(
            "/api/web/v1/knowbot-requests", json=request_body(), headers=WEB
        )
        request_id = made.json()["request_id"]

        c.cookies.set(ADMIN_SID, "admin")
        detail = (await c.get(f"/api/admin/v1/knowbot-requests/{request_id}")).json()
        assert detail["idea"]["kind"] == "six_questions"
        assert detail["owner_subject"] == "user-123"
        draft = await c.post(
            f"/api/admin/v1/knowbot-requests/{request_id}/drafts",
            json={"manifest": manifest()},
            headers=ADMIN,
        )
        assert draft.status_code == 201
        draft_id = draft.json()["draft_id"]
        base = f"/api/admin/v1/knowbot-drafts/{draft_id}"
        test = {"budget_limit": "20", "timeout_seconds": 600}

        early = await c.post(f"{base}/test", json=test, headers=ADMIN)
        assert early.status_code == 409
        assert early.json()["code"] == "rules_not_reviewed"
        assert (await c.post(f"{base}/rules-review")).status_code == 403
        reviewed = await c.post(f"{base}/rules-review", headers=ADMIN)
        assert reviewed.status_code == 204
        listed = (
            await c.get("/api/admin/v1/knowbot-drafts?status=pending_test")
        ).json()
        assert listed["drafts"][0]["rules_reviewed_by"] == "user-123"
        assert listed["drafts"][0]["rule_sources"][0]["source"] == "user_text"

        started = await c.post(f"{base}/test", json=test, headers=ADMIN)
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        runtime.results[run_id] = result(run_id, "passed")
        refreshed = (await c.post(f"{base}/refresh", headers=ADMIN)).json()
        assert refreshed["status"] == "delivered"
        assert refreshed["knowbot_id"] == "kb-1"
        assert len(knowmart.delivered) == 1

        # 用户看到的：状态与运行正确性报告，没有清单与方法原文
        mine = (await c.get("/api/web/v1/knowbot-requests")).json()["requests"]
        assert mine[0]["drafts"][0]["status"] == "delivered"
        assert mine[0]["drafts"][0]["report"]["status"] == "passed"
        rendered = str(mine)
        assert "manifest" not in rendered and "rule_sources" not in rendered
        assert "owner_subject" not in rendered and "idea" not in rendered


@pytest.mark.asyncio
async def test_users_only_see_their_own_requests(wired) -> None:
    svc, *_ = wired
    from app.domain.knowbots.drafts import KnowbotRequestIn

    await svc.submit("someone-else", KnowbotRequestIn.model_validate(request_body()))
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        assert (await c.get("/api/web/v1/knowbot-requests")).json()["requests"] == []


@pytest.mark.asyncio
async def test_unknown_ids_are_404(wired) -> None:
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        missing = await c.post(
            "/api/admin/v1/knowbot-drafts/not-a-uuid/refresh", headers=ADMIN
        )
        assert missing.status_code == 404
        assert (await c.get("/api/admin/v1/knowbot-requests/x")).status_code == 404


@pytest.mark.asyncio
async def test_my_data_upload_list_delete_and_admin_sees_metadata_only(wired) -> None:
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        files = {"file": ("年报.pdf", b"%PDF-1.7 test", "application/pdf")}
        no_csrf = await c.post("/api/web/v1/knowbot-datasets", files=files)
        assert no_csrf.status_code == 403
        made = await c.post("/api/web/v1/knowbot-datasets", files=files, headers=WEB)
        assert made.status_code == 201, made.text
        dataset = made.json()["dataset"]
        assert dataset["name"] == "年报.pdf" and dataset["version"] == "1"
        bad = await c.post(
            "/api/web/v1/knowbot-datasets",
            files={"file": ("x.exe", b"MZ", "application/octet-stream")},
            headers=WEB,
        )
        assert (
            bad.status_code == 400 and bad.json()["code"] == "dataset_type_not_allowed"
        )
        mine = (await c.get("/api/web/v1/knowbot-datasets")).json()
        assert [d["dataset_id"] for d in mine["datasets"]] == [dataset["dataset_id"]]
        assert mine["used_bytes"] == len(b"%PDF-1.7 test")
        assert mine["quota_bytes"] == 1024**3

        request_id = (
            await c.post(
                "/api/web/v1/knowbot-requests", json=request_body(), headers=WEB
            )
        ).json()["request_id"]
        c.cookies.set(ADMIN_SID, "admin")
        detail = (await c.get(f"/api/admin/v1/knowbot-requests/{request_id}")).json()
        assert [d["dataset_id"] for d in detail["datasets"]] == [dataset["dataset_id"]]
        assert "object_key" not in str(detail) and "version_id" not in str(detail)

        url = f"/api/web/v1/knowbot-datasets/{dataset['dataset_id']}"
        assert (await c.delete(url)).status_code == 403
        assert (await c.delete(url, headers=WEB)).status_code == 204
        assert (await c.delete(url, headers=WEB)).status_code == 404
