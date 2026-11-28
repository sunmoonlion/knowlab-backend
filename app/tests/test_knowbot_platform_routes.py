"""切换的接口（SDD 0046 第四节）：作者请平台孵化、我自己来、续期、交平台测与收回；运营
轻受理、交还；默认模式；会话、CSRF 与权限。"""

from __future__ import annotations

import pytest

from tests.knowbot_fakes import OWNER, manifest, request_body, single_method_planning
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
        from app.domain.knowbots.drafts import KnowbotRequestIn

        await svc.submit(
            OWNER,
            KnowbotRequestIn.model_validate(
                request_body(mode="self", budget_limit=None)
            ),
        )
        c.cookies.set(ADMIN_SID, "admin")
        # 交平台测：自助的准专家
        draft = await repo.create_draft(
            request_id=repo.requests[next(iter(repo.requests))].id,
            request_version=1,
            revision=1,
            version="1",
            manifest=manifest(),
            mode="self",
        )
        from app.application.services.knowbot_platform import KnowbotPlatformService
        from app.domain.knowbots.platform_grants import PlatformTestIn

        gid = (
            await KnowbotPlatformService(repo).ask_platform_test(
                OWNER, str(draft.id), PlatformTestIn(agree=True, budget_limit="5")
            )
        ).id
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

        async def queues():
            counts = (await c.get("/api/admin/v1/knowbot-counts")).json()["counts"]
            rows = (await c.get("/api/admin/v1/knowbot-board/tests")).json()["items"]
            return counts["acceptance"], counts["tests"], [r["category"] for r in rows]

        assert await queues() == (1, 0, [])
        decision = f"/api/admin/v1/knowbot-grants/{gid}/decision"
        c.cookies.set(ADMIN_SID, "base")  # 轻受理要 knowlab:release
        assert (
            await c.post(decision, json={"accepted": True}, headers=ADMIN)
        ).status_code == 403
        c.cookies.set(ADMIN_SID, "admin")
        ok = await c.post(decision, json={"accepted": True}, headers=ADMIN)
        assert ok.json()["grant"]["state"] == "active"
        assert await queues() == (0, 1, ["to_start"])
    assert repo.requests[next(iter(repo.requests))].owner_subject == OWNER


@pytest.mark.asyncio
async def test_hand_back_through_the_api(wired) -> None:  # noqa: F811
    svc, repo, *_ = wired
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        from app.domain.knowbots.drafts import KnowbotRequestIn

        rid = str(
            (
                await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
            ).id
        )
        c.cookies.set(ADMIN_SID, "admin")
        req = f"/api/admin/v1/knowbot-requests/{rid}"
        await c.post(f"{req}/versions/1/acceptance", json=ALL_CLEAR, headers=ADMIN)
        assert (await c.post(f"{req}/hand-back", headers=ADMIN)).status_code == 204
        assert (await c.get(req)).status_code == 404
        assert repo.requests[next(iter(repo.requests))].incubation_mode == "self"


STEP = {
    "selection": {"applies_when": "交付结果时", "required": True, "max_calls": 1},
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
        from app.domain.knowbots.drafts import KnowbotRequestIn

        rid = str(
            (
                await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
            ).id
        )
        req = f"/api/admin/v1/knowbot-requests/{rid}"
        assert (await c.get(f"{req}/workdraft")).status_code == 404  # 还没受理
        await c.post(f"{req}/versions/1/acceptance", json=ALL_CLEAR, headers=ADMIN)
        from app.application.services.knowbot_workdraft import KnowbotWorkdraftService

        await KnowbotWorkdraftService(repo).save(
            OWNER,
            rid,
            revision=0,
            edits=[
                ("scope/does", "A 股非金融公司的财务分析"),
                ("steps/overview", STEP),
                ("planning", single_method_planning("overview")),
            ],
        )
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
        from app.application.services.knowbot_proposals import KnowbotProposalService

        await KnowbotProposalService(repo).decide(
            OWNER, rid, shown["proposal_id"], action="accept", revision=1
        )
        # 运营出一版：平台模式
        preview = (await c.get(f"{req}/workdraft/preview")).json()
        assert preview["problems"] == []
        published = await c.post(
            f"{req}/workdraft/publish",
            headers=ADMIN,
            json={"preview_token": preview["preview_token"]},
        )
        assert published.status_code == 201, published.text
        assert published.json()["mode"] == "platform"
        # 老的「贴清单」路由没了
        gone = await c.post(f"{req}/drafts", json={"manifest": {}}, headers=ADMIN)
        assert gone.status_code in (404, 405)
