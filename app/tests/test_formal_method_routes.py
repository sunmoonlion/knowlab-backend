"""Browser formal incubation cannot submit its own successful validation report."""

import httpx
import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.interfaces.endpoints import knowbot_routes
from app.main import app
from tests.test_auth_routes_security import FakeAuthService
from tests.test_auth_routes_security import session as browser_session
from tests.test_formal_method_service import OWNER, freeze_command, setup
from tests.test_knowbot_workdraft_routes import ADMIN_SID, WEB, WEB_SID


@pytest.mark.asyncio
async def test_formal_author_http_flow_checks_identity_csrf_and_trusted_receipt(
    monkeypatch,
):
    repo, runtime, service, mid, command = await setup()
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
    app.dependency_overrides[knowbot_routes.formal_service] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            validate_url = f"/api/web/v1/method-units/{mid}/validations"
            freeze_url = f"/api/web/v1/method-units/{mid}/formal-versions/1"
            payload = command.model_dump(mode="json")
            assert (await client.post(validate_url, json=payload)).status_code == 401
            client.cookies.set(ADMIN_SID, "admin")
            assert (await client.post(validate_url, json=payload)).status_code == 401
            client.cookies.set(WEB_SID, "member")
            assert (await client.post(validate_url, json=payload)).status_code == 403
            assert (
                await client.post(
                    validate_url, json={**payload, "passed": True}, headers=WEB
                )
            ).status_code == 422
            pending = await client.post(validate_url, json=payload, headers=WEB)
            assert pending.status_code == 200, pending.text
            assert pending.json()["status"] == "pending"
            freeze = freeze_command(command).model_dump(mode="json")
            assert (
                await client.put(
                    freeze_url, json={**freeze, "evidence_id": "made-up"}, headers=WEB
                )
            ).status_code == 422
            assert (
                await client.put(freeze_url, json=freeze, headers=WEB)
            ).status_code == 409
            runtime.complete(command.run_id)
            status = await client.get(
                validate_url + f"/{command.run_id}",
                params={"request_id": str(command.request_id)},
            )
            assert status.status_code == 200 and status.json()["status"] == "passed"
            saved = await client.put(freeze_url, json=freeze, headers=WEB)
            assert saved.status_code == 200, saved.text
            assert saved.headers["cache-control"] == "no-store"
            assert saved.json()["provenance"]["validation"]["owner_subject"] == OWNER
            assert (await client.get(freeze_url)).json() == saved.json()
            assert len(repo.method_versions) == 1
    finally:
        app.dependency_overrides.pop(knowbot_routes.formal_service, None)
