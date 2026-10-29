"""切换的接口（SDD 0046 第四节）：作者请平台孵化、我自己来、续期、交平台测与收回；运营
轻受理、交还；默认模式；会话、CSRF 与权限。"""

from __future__ import annotations

import pytest

from tests.knowbot_fakes import OWNER, manifest, request_body
from tests.test_knowbot_creation_routes import (  # noqa: F401
    ADMIN,
    ADMIN_SID,
    ALL_CLEAR,
    WEB,
    WEB_SID,
    client,
    wired,
)


@pytest.mark.asyncio
async def test_switching_through_the_api(wired) -> None:  # noqa: F811
    svc, repo, *_ = wired
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        assert (await c.get("/api/web/v1/knowbot-requests/defaults")).json()[
            "mode"
        ] is None
        body = request_body(mode="self")
        body.pop("budget_limit")
        made = await c.post("/api/web/v1/knowbot-requests", json=body, headers=WEB)
        assert made.status_code == 201, made.text
        assert made.json()["incubation_mode"] == "self"
        rid = made.json()["request_id"]
        assert (await c.get("/api/web/v1/knowbot-requests/defaults")).json()[
            "mode"
        ] == "self"
        # 自助：运营看不到
        c.cookies.set(ADMIN_SID, "admin")
        req = f"/api/admin/v1/knowbot-requests/{rid}"
        assert (await c.get(req)).status_code == 404
        # 请平台孵化：没 CSRF 不行，没同意不行
        ask = f"/api/web/v1/knowbot-requests/{rid}/platform"
        agree = {"agree": True, "budget_limit": "20"}
        assert (await c.post(ask, json=agree)).status_code == 403
        bad = await c.post(
            ask, json={"agree": False, "budget_limit": "20"}, headers=WEB
        )
        assert bad.status_code == 422
        asked = await c.post(ask, json=agree, headers=WEB)
        assert asked.status_code == 201
        assert asked.json()["grant"]["state"] == "pending"
        assert asked.json()["grant"]["basis"].startswith("platform:knowlab:incubate:")
        mine = (await c.get(f"/api/web/v1/knowbot-requests/{rid}")).json()
        assert mine["incubation_mode"] == "platform"
        assert [g["kind"] for g in mine["grants"]] == ["incubate"]
        # 运营受理 → 生效；详情看得到
        url = f"{req}/versions/1/acceptance"
        assert (await c.post(url, json=ALL_CLEAR, headers=ADMIN)).status_code == 201
        detail = (await c.get(req)).json()
        assert detail["grants"][0]["state"] == "active"
        # 续期、我自己来
        renew = await c.post(f"{ask}/renew", headers=WEB)
        assert renew.status_code == 200
        back = await c.post(
            f"/api/web/v1/knowbot-requests/{rid}/take-back", headers=WEB
        )
        assert back.status_code == 204
        assert (await c.get(req)).status_code == 404

        # 交平台测：自助的准专家
        draft = await repo.create_draft(
            request_id=repo.requests[next(iter(repo.requests))].id,
            request_version=1,
            revision=1,
            version="1",
            manifest=manifest(),
            mode="self",
        )
        test_ask = f"/api/web/v1/knowbot-drafts/{draft.id}/platform-test"
        got = await c.post(
            test_ask, json={"agree": True, "budget_limit": "5"}, headers=WEB
        )
        assert got.status_code == 201, got.text
        gid = got.json()["grant"]["grant_id"]
        [light] = [
            i
            for i in (await c.get("/api/admin/v1/knowbot-board/acceptance")).json()[
                "items"
            ]
            if i["acceptance_kind"] == "light"
        ]
        assert (light["category"], light["draft"]["draft_id"]) == (
            "light_pending",
            str(draft.id),
        )
        decision = f"/api/admin/v1/knowbot-grants/{gid}/decision"
        c.cookies.set(ADMIN_SID, "base")  # 轻受理要 knowlab:release
        assert (
            await c.post(decision, json={"accepted": True}, headers=ADMIN)
        ).status_code == 403
        c.cookies.set(ADMIN_SID, "admin")
        ok = await c.post(decision, json={"accepted": True}, headers=ADMIN)
        assert ok.json()["grant"]["state"] == "active"
        withdrawn = await c.post(
            f"/api/web/v1/knowbot-grants/{gid}/withdraw", headers=WEB
        )
        assert withdrawn.status_code == 204
    assert repo.requests[next(iter(repo.requests))].owner_subject == OWNER


@pytest.mark.asyncio
async def test_hand_back_through_the_api(wired) -> None:  # noqa: F811
    svc, repo, *_ = wired
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        made = await c.post(
            "/api/web/v1/knowbot-requests", json=request_body(), headers=WEB
        )
        rid = made.json()["request_id"]
        c.cookies.set(ADMIN_SID, "admin")
        req = f"/api/admin/v1/knowbot-requests/{rid}"
        await c.post(f"{req}/versions/1/acceptance", json=ALL_CLEAR, headers=ADMIN)
        assert (await c.post(f"{req}/hand-back", headers=ADMIN)).status_code == 204
        assert (await c.get(req)).status_code == 404
        assert repo.requests[next(iter(repo.requests))].incubation_mode == "self"


STEP = {
    "id": "overview",
    "title": "数据摸底",
    "does": "看清数据有哪些表、哪些报告期。",
    "why": "先知道手上有什么。",
    "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
    "outputs": [{"name": "periods", "type": "list"}],
    "checks": [{"template": "list_min", "params": {"path": "periods"}}],
}


@pytest.mark.asyncio
async def test_operators_propose_and_publish_through_the_api(wired) -> None:  # noqa: F811
    svc, repo, *_ = wired
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        c.cookies.set(ADMIN_SID, "admin")
        made = await c.post(
            "/api/web/v1/knowbot-requests", json=request_body(), headers=WEB
        )
        rid = made.json()["request_id"]
        req = f"/api/admin/v1/knowbot-requests/{rid}"
        assert (await c.get(f"{req}/workdraft")).status_code == 404  # 还没受理
        await c.post(f"{req}/versions/1/acceptance", json=ALL_CLEAR, headers=ADMIN)
        mine = f"/api/web/v1/knowbot-requests/{rid}/workdraft"
        saved = await c.post(
            f"{mine}/cells",
            json={
                "revision": 0,
                "cells": [
                    {"path": "scope/does", "value": "A 股非金融公司的财务分析"},
                    {"path": "steps/overview", "value": STEP},
                ],
            },
            headers=WEB,
        )
        assert saved.status_code == 200, saved.text
        proposed = await c.post(
            f"{req}/workdraft/proposals",
            json={
                "proposals": [
                    {
                        "path": "steps/overview/why",
                        "op": "set",
                        "value": "心里有数。",
                        "reason": "更短",
                    }
                ]
            },
            headers=ADMIN,
        )
        assert proposed.status_code == 201, proposed.text
        [shown] = proposed.json()["proposals"]
        assert shown["proposed_by"]  # 管理端看得到谁提的
        # 作者在 web端看到、接受
        view = (await c.get(mine)).json()
        [pending] = view["proposals"]
        assert "proposed_by" not in pending and pending["state"] == "pending"
        decided = await c.post(
            f"{mine}/proposals/{pending['proposal_id']}",
            json={"action": "accept", "revision": view["revision"]},
            headers=WEB,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["proposal"]["state"] == "accepted"
        # 运营出一版：平台模式
        preview = (await c.get(f"{req}/workdraft/preview")).json()
        assert preview["problems"] == []
        published = await c.post(f"{req}/workdraft/publish", headers=ADMIN)
        assert published.status_code == 201, published.text
        assert published.json()["mode"] == "platform"
        # 老的「贴清单」路由没了
        gone = await c.post(f"{req}/drafts", json={"manifest": {}}, headers=ADMIN)
        assert gone.status_code in (404, 405)
