"""Author APIs use normal web identity/CSRF and never expose a publish shortcut."""

import uuid

import httpx
import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_methods import KnowbotMethodService
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.interfaces.endpoints import knowbot_routes
from app.main import app
from tests.test_auth_routes_security import FakeAuthService
from tests.test_auth_routes_security import session as browser_session
from tests.test_author_methods_db import binding, command, publish, source
from tests.test_knowbot_repository_db import db as db
from tests.test_knowbot_workdraft_routes import ADMIN_SID, WEB, WEB_SID

pytestmark = pytest.mark.asyncio


async def test_author_api_csrf_freeze_read_and_preview_without_mutation(
    db, monkeypatch
):
    rid, mid = await source(db), uuid.uuid4()
    monkeypatch.setattr(
        auth_middleware,
        "web_auth_service",
        FakeAuthService({"member": browser_session("web", "profile:read")}),
    )
    monkeypatch.setattr(
        auth_middleware,
        "admin_auth_service",
        FakeAuthService({"admin": browser_session("admin", "knowlab:admin")}),
    )
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotMethodService(repo)
        app.dependency_overrides[knowbot_routes.method_service] = lambda: service
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                url = f"/api/web/v1/method-units/{mid}/versions/1"
                payload = command(rid).model_dump(mode="json")
                assert (await client.get(url)).status_code == 401
                client.cookies.set(ADMIN_SID, "admin")
                assert (await client.get(url)).status_code == 401
                client.cookies.set(WEB_SID, "member")
                assert (await client.put(url, json=payload)).status_code == 403
                forged = {**payload, "owner_subject": "someone-else"}
                assert (
                    await client.put(url, json=forged, headers=WEB)
                ).status_code == 422
                response = await client.put(url, json=payload, headers=WEB)
                assert response.status_code == 200, response.text
                saved = response.json()
                assert saved["method_id"] == str(mid) and saved["version"] == 1
                assert saved["definition"]["step"]["uses"] == ["records"]
                assert response.headers["cache-control"] == "no-store"
                assert (await client.get(url)).json() == saved
                assert (
                    await client.put(url, json=payload, headers=WEB)
                ).json() == saved
                listing = await client.get("/api/web/v1/method-units")
                assert listing.json()["items"][0]["method_id"] == str(mid)
                assert (
                    await client.get("/api/web/v1/method-units?limit=101")
                ).status_code == 422
                record = await service.get("user-123", str(mid), 1)
                preview = binding(rid, record).model_dump(mode="json")
                assert (
                    await client.post(url + "/preview-binding", json=preview)
                ).status_code == 403
                result = await client.post(
                    url + "/preview-binding", json=preview, headers=WEB
                )
                assert (
                    result.status_code == 200 and result.json()["preview_only"] is True
                )
                assert (await repo.get_workdraft(rid)).revision == 1
        finally:
            app.dependency_overrides.pop(knowbot_routes.method_service, None)


async def test_method_routes_hide_another_authors_version_and_block_overwrite(
    db, monkeypatch
):
    rid = await source(db, owner="someone-else")
    mid = uuid.uuid4()
    await publish(db, mid, rid, owner="someone-else")
    monkeypatch.setattr(
        auth_middleware,
        "web_auth_service",
        FakeAuthService({"member": browser_session("web", "profile:read")}),
    )
    async with db() as session:
        service = KnowbotMethodService(SqlKnowlabRepository(session))
        app.dependency_overrides[knowbot_routes.method_service] = lambda: service
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                client.cookies.set(WEB_SID, "member")
                url = f"/api/web/v1/method-units/{mid}/versions/1"
                assert (await client.get(url)).status_code == 404
                assert (
                    await client.put(
                        url, json=command(rid).model_dump(mode="json"), headers=WEB
                    )
                ).status_code == 404
                assert (await client.get("/api/web/v1/method-units")).json()[
                    "items"
                ] == []
        finally:
            app.dependency_overrides.pop(knowbot_routes.method_service, None)
