"""Release authority is enforced server-side; web identity cannot approve."""

import httpx

import app.interfaces.http.middleware.auth as auth_middleware
from app.interfaces.endpoints import knowbot_routes
from app.main import app, settings
from tests.test_auth_routes_security import FakeAuthService
from tests.test_auth_routes_security import session as browser_session
from tests.test_knowbot_repository_db import db as db
from tests.test_knowbot_workdraft_routes import ADMIN_SID, CSRF, WEB, WEB_SID
from tests.test_manual_release_db import approve, candidate, manual


async def test_manual_release_roles_csrf_owner_and_exact_package(db, monkeypatch):
    _, _, _, draft, payload = await candidate(db)
    monkeypatch.setattr(
        auth_middleware,
        "web_auth_service",
        FakeAuthService(
            {
                "member": browser_session("web", "profile:read"),
            }
        ),
    )
    monkeypatch.setattr(
        auth_middleware,
        "admin_auth_service",
        FakeAuthService(
            {
                "admin": browser_session("admin", "knowlab:admin"),
                "releaser": browser_session(
                    "admin", "knowlab:admin", "knowlab:release"
                ),
            }
        ),
    )
    async with db() as session:
        app.dependency_overrides[knowbot_routes.manual_release_service] = lambda: (
            manual(session)
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                url = f"/api/web/v1/knowbot-drafts/{draft.id}/manual-release"
                data = payload.model_dump(mode="json")
                assert (
                    await client.post(url, json=data, headers=WEB)
                ).status_code == 401
                client.cookies.set(WEB_SID, "member")
                assert (await client.post(url, json=data)).status_code == 403
                forged = data | {"approved_by": "admin"}
                assert (
                    await client.post(url, json=forged, headers=WEB)
                ).status_code == 422
                response = await client.post(url, json=data, headers=WEB)
                assert response.status_code == 201, response.text
                identifier = response.json()["id"]
                admin_url = f"/api/admin/v1/knowbot-manual-releases/{identifier}"
                assert (await client.get(admin_url)).status_code == 401
                client.cookies.set(ADMIN_SID, "admin")
                assert (await client.get(admin_url)).status_code == 403
                client.cookies.set(ADMIN_SID, "releaser")
                detail = await client.get(admin_url)
                assert detail.status_code == 200, detail.text
                assert detail.json()["manifest"] == draft.manifest
                decision = admin_url + "/decision"
                body = approve().model_dump(mode="json")
                assert (await client.post(decision, json=body)).status_code == 403
                headers = {
                    "Origin": settings.browser_profile("admin").frontend_origins[0],
                    "X-CSRF-Token": CSRF,
                }
                result = await client.post(decision, json=body, headers=headers)
                assert result.status_code == 200, result.text
                assert result.json()["status"] == "approved"
                assert result.json()["decided_by"] == "user-123"
        finally:
            app.dependency_overrides.pop(knowbot_routes.manual_release_service, None)
