"""发版前补齐 L3–L5 的接口（web端，作者本人）：知识库栏、挑数据、一键接受一步、带测试
报告的一轮、模型 key 没填时发起前拦、名称页带平台在问什么。会话与 CSRF、不是自己的
404 同其他接口。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_library import KnowbotLibraryService
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.incubation import IncubationResultV1
from app.interfaces.endpoints import knowbot_routes
from app.interfaces.http.middleware.auth import get_web_knowbase_token_provider
from app.main import app
from tests.knowbot_fakes import (
    MATERIAL,
    OWNER,
    FakeIncubator,
    FakeLibrary,
    FakeRuntime,
    MemoryKnowlab,
    own_computer,
    request_body,
)
from tests.test_auth_routes_security import FakeAuthService, session
from tests.test_knowbot_workdraft_routes import ADMIN_SID, WEB, WEB_SID, client

HANDBACK: dict[str, Any] = {
    "sources": [
        {"path": "steps/scan/title", "kind": "proposal"},
        {"path": "steps/scan/why", "kind": "proposal"},
    ],
    "suggestions": [
        {
            "id": "s1",
            "path": "steps/scan/title",
            "op": "set",
            "value": "数据摸底",
            "reason": "起个名字",
        },
        {
            "id": "s2",
            "path": "steps/scan/why",
            "op": "set",
            "value": "先看手上有什么",
            "reason": "写明为什么",
        },
    ],
}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    repo, runtime, incubator = MemoryKnowlab(), FakeRuntime(), FakeIncubator()
    knowbase = FakeLibrary()
    own_computer(repo)
    svc = KnowbotCreationService(repo, runtime)
    web = FakeAuthService({"member": session("web", "profile:read")})
    admin = FakeAuthService({"admin": session("admin", "knowlab:admin")})
    monkeypatch.setattr(auth_middleware, "web_auth_service", web)
    monkeypatch.setattr(auth_middleware, "admin_auth_service", admin)

    async def provide() -> str:
        return "user-token"

    app.dependency_overrides[get_web_knowbase_token_provider] = lambda: provide
    app.dependency_overrides[knowbot_routes.service] = lambda: svc
    app.dependency_overrides[knowbot_routes.workdraft_service] = lambda: (
        KnowbotWorkdraftService(repo)
    )
    app.dependency_overrides[knowbot_routes.library_service] = lambda: (
        KnowbotLibraryService(repo, knowbase)
    )
    app.dependency_overrides[knowbot_routes.incubation_service] = lambda: (
        KnowbotIncubationService(repo, incubator, runtime)
    )
    app.dependency_overrides[knowbot_routes.board_service] = lambda: KnowbotBoard(repo)
    app.dependency_overrides[knowbot_routes.proposal_service] = lambda: (
        KnowbotProposalService(repo)
    )
    yield svc, repo, runtime, incubator, knowbase
    app.dependency_overrides.clear()


async def _mine(svc: KnowbotCreationService, owner: str = OWNER):
    return await svc.submit(
        owner,
        KnowbotRequestIn.model_validate(request_body(mode="self", budget_limit=None)),
    )


async def test_the_knowledge_column_needs_my_session(wired) -> None:
    _, _, _, _, knowbase = wired
    async with client() as c:
        assert (await c.get("/api/web/v1/knowbot-library")).status_code == 401
        c.cookies.set(ADMIN_SID, "admin")
        assert (await c.get("/api/web/v1/knowbot-library")).status_code == 401
        c.cookies.set(WEB_SID, "member")
        got = await c.get("/api/web/v1/knowbot-library")
    assert got.status_code == 200
    (one,) = got.json()["materials"]
    assert (one["material_id"], one["name"]) == (MATERIAL, "2024 年报.pdf")
    assert knowbase.tokens == ["user-token"]


async def test_pick_data_then_round_then_accept_a_step(wired) -> None:
    svc, repo, _, incubator, _ = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        assert (
            await c.put(f"{base}/data-picks", json={"revision": 0, "picks": []})
        ).status_code == 403  # 没带 CSRF
        bad = await c.put(
            f"{base}/data-picks",
            json={"revision": 0, "picks": ["m-x"]},
            headers=WEB,
        )
        assert (bad.status_code, bad.json()["code"]) == (400, "pick_not_shown")
        picked = await c.put(
            f"{base}/data-picks",
            json={"revision": 0, "picks": [MATERIAL]},
            headers=WEB,
        )
        assert picked.status_code == 200
        view = picked.json()
        assert view["body"]["data_picks"] == [MATERIAL]
        assert view["data_formats"][MATERIAL]["name"] == "2024 年报.pdf"
        assert (await c.get(base)).json()["data_formats"][MATERIAL]["readings"] == [
            "pages",
            "text",
        ]
        saved = await c.post(
            f"{base}/cells",
            json={
                "revision": 1,
                "cells": [{"path": "steps", "value": [{"id": "scan"}]}],
            },
            headers=WEB,
        )
        assert saved.status_code == 200
        started = await c.post(
            f"{base}/incubations", json={"action": "draft"}, headers=WEB
        )
        assert started.status_code == 201
        round_id = started.json()["incubation"]["incubation_id"]
        record = await repo.get_incubation(uuid.UUID(round_id))
        assert record is not None
        incubator.results[record.run_id] = IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": record.run_id,
                "status": "succeeded",
                "handback": HANDBACK,
            }
        )
        await c.post(f"{base}/incubations/{round_id}/refresh", headers=WEB)
        accepted = await c.post(
            f"{base}/incubations/{round_id}/steps/scan/accept-all",
            json={"revision": 2},
            headers=WEB,
        )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["accepted"] == ["s1", "s2"] and body["skipped"] == []
    assert body["workdraft"]["body"]["steps"][0]["title"] == "数据摸底"
    assert incubator.started[0].data_formats[0].name == "2024 年报.pdf"


async def test_no_model_key_is_said_before_starting(wired) -> None:
    svc, _, runtime, incubator, _ = wired
    request = await _mine(svc)
    base = f"/api/web/v1/knowbot-requests/{request.id}/workdraft"
    runtime.key_ready = False
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        listed = await c.get(f"{base}/computers")
        assert listed.json()["model_key"] is False
        refused = await c.post(
            f"{base}/incubations", json={"action": "draft"}, headers=WEB
        )
        assert (refused.status_code, refused.json()["code"]) == (
            409,
            "model_key_missing",
        )
        no_report = await c.post(
            f"{base}/incubations",
            json={"action": "revise", "instruction": "照报告改", "with_test": True},
            headers=WEB,
        )
        assert no_report.status_code == 409
    assert incubator.started == []


async def test_the_name_page_says_what_the_platform_asked(wired) -> None:
    svc, _, _, _, _ = wired
    request = await _mine(svc)
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        got = await c.get(f"/api/web/v1/knowbot-requests/{request.id}")
    assert got.status_code == 200
    assert got.json()["platform_asks"] == []
