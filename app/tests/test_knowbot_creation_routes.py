"""分身创建的接口：会话与权限、所有者隔离、CSRF、报告不带分析内容。"""

from __future__ import annotations

import httpx
import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_questions import KnowbotQuestionService
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
    waiting,
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
            "admin": session("admin", "knowlab:admin", "knowlab:release"),
            # 运营：只有基本权限（白名单角色 operator 换算出来的就是这个，见 auth）
            "operator": session("admin", "knowlab:admin"),
            "no-scope": session("admin", "profile:read"),
        }
    )
    monkeypatch.setattr(auth_middleware, "web_auth_service", web)
    monkeypatch.setattr(auth_middleware, "admin_auth_service", admin)
    monkeypatch.setattr(knowbot_routes, "runtime_clients", lambda: runtime)
    app.dependency_overrides[knowbot_routes.service] = lambda: svc
    data = KnowbotDataService(repo, MemoryDataStore())
    app.dependency_overrides[knowbot_routes.data_service] = lambda: data
    asks = KnowbotQuestionService(repo, runtime)
    app.dependency_overrides[knowbot_routes.question_service] = lambda: asks
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
            "knowbench"
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
async def test_operators_cannot_draft_review_deliver_or_reset(wired) -> None:
    """运营只能看、发起测试、答跑法问题；建草稿、核出处、交付、退回只留给 admin。"""
    svc, *_ = wired
    from app.domain.knowbots.drafts import KnowbotRequestIn

    request = await svc.submit(
        "user-123", KnowbotRequestIn.model_validate(request_body())
    )
    draft = await svc.add_draft(str(request.id), manifest())
    async with client() as c:
        c.cookies.set(ADMIN_SID, "operator")
        assert (await c.get("/api/admin/v1/knowbot-drafts")).status_code == 200
        base = f"/api/admin/v1/knowbot-drafts/{draft.id}"
        for path, body in [
            (f"/api/admin/v1/knowbot-requests/{request.id}/drafts", manifest()),
            (f"{base}/rules-review", None),
            (f"{base}/deliver", None),
            (f"{base}/reset", None),
        ]:
            json = {"manifest": body} if body else None
            refused = await c.post(path, json=json, headers=ADMIN)
            assert refused.status_code == 403, path
        # 核过出处之后，发起测试运营可以做
        await svc.review_rules(str(draft.id), "admin-1")
        started = await c.post(
            f"{base}/test",
            json={"budget_limit": "20", "timeout_seconds": 600},
            headers=ADMIN,
        )
        assert started.status_code == 200, started.text


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
async def test_admin_sees_how_far_a_running_test_got_and_users_do_not(wired) -> None:
    """测试中看进度（待办账 135）：管理端草稿上带进度，跑完就没有；用户的结果不变。"""
    svc, repo, runtime, _ = wired
    from app.domain.knowbots.drafts import KnowbotRequestIn

    request = await svc.submit(
        "user-123", KnowbotRequestIn.model_validate(request_body())
    )
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    progress = {
        "case_id": "c1",
        "cases_done": 0,
        "cases_total": 1,
        "step": 2,
        "steps_total": 2,
        "step_title": "回答",
        "attempt": 1,
        "spent": "0.120000",
    }
    runtime.results[run.run_id] = result(run.run_id, "running", progress=progress)
    await svc.refresh(str(draft.id))
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        [shown] = (await c.get("/api/admin/v1/knowbot-drafts")).json()["drafts"]
        assert shown["status"] == "testing"
        assert shown["progress"] == progress | {"queued_behind": None}
        assert shown["run_status"] == "running"
        c.cookies.set(WEB_SID, "member")
        mine = (await c.get("/api/web/v1/knowbot-requests")).json()["requests"]
        assert "progress" not in str(mine)
        runtime.results[run.run_id] = result(run.run_id, "passed")
        await svc.refresh(str(draft.id))
        [done] = (await c.get("/api/admin/v1/knowbot-drafts")).json()["drafts"]
        assert done["status"] == "delivered" and done["progress"] is None
        assert done["run_status"] is None and done["run_running_since"] is None


@pytest.mark.asyncio
async def test_admin_sees_a_test_that_was_accepted_but_has_not_started(wired) -> None:
    """运行方受理了还没开跑：管理端拿到「已受理」、没有开跑时间（账 136 第 ②）。

    不拿我们提交的时间冒充开跑时间；排在测试账号别的委托后面时，进度里带那件委托的编号
    （0034 §三.3）。
    """
    svc, repo, runtime, _ = wired
    from app.domain.knowbots.drafts import KnowbotRequestIn

    request = await svc.submit(
        "user-123", KnowbotRequestIn.model_validate(request_body())
    )
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[run.run_id] = result(run.run_id, "accepted")
    await svc.refresh(str(draft.id))
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        [shown] = (await c.get("/api/admin/v1/knowbot-drafts")).json()["drafts"]
        assert shown["run_status"] == "accepted"
        assert shown["run_running_since"] is None
        behind = "03e447a0-0000-4000-8000-000000000000"
        queued = {
            "case_id": "c1",
            "cases_done": 0,
            "cases_total": 3,
            "step": 0,
            "steps_total": 8,
            "spent": "0",
            "queued_behind": behind,
        }
        runtime.results[run.run_id] = result(
            run.run_id,
            "running",
            started_at="2026-10-19T00:49:14Z",
            progress=queued,
        )
        await svc.refresh(str(draft.id))
        [shown] = (await c.get("/api/admin/v1/knowbot-drafts")).json()["drafts"]
        assert shown["run_running_since"].startswith("2026-10-19T00:49:14")
        assert shown["progress"]["queued_behind"] == behind


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


@pytest.mark.asyncio
async def test_questions_through_the_api(wired) -> None:
    svc, repo, runtime, _ = wired
    from app.domain.knowbots.drafts import KnowbotRequestIn

    request = await svc.submit(
        "user-123", KnowbotRequestIn.model_validate(request_body())
    )
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[run.run_id] = waiting(run.run_id)
    await svc.refresh(str(draft.id))
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        listed = (await c.get("/api/admin/v1/knowbot-questions")).json()["questions"]
        ids = {q["kind"]: q["question_id"] for q in listed}
        assert set(ids) == {"tool_approval", "ask_before", "question"}
        # 审批归我们；判断与开步前的提问建出来就归用户（审读第 2 条）
        assert {q["kind"]: q["route"] for q in listed} == {
            "tool_approval": None,
            "ask_before": "user",
            "question": "user",
        }
        base = "/api/admin/v1/knowbot-questions"
        take = f"{base}/{ids['ask_before']}/take"
        assert (await c.post(take, json={"reason": "x"})).status_code == 403
        assert (
            await c.post(take, json={"reason": ""}, headers=ADMIN)
        ).status_code == 422
        taken = await c.post(take, json={"reason": "跑法：取几年"}, headers=ADMIN)
        assert taken.status_code == 204
        back = await c.post(f"{base}/{ids['ask_before']}/to-user", headers=ADMIN)
        assert back.status_code == 204
        relisted = (await c.get(base)).json()["questions"]
        assert {q["kind"]: q["route_reason"] for q in relisted}["ask_before"] is None
        approve = await c.post(
            f"{base}/{ids['tool_approval']}/answer",
            json={"decision": "approve"},
            headers=ADMIN,
        )
        assert approve.status_code == 200 and approve.json()["sent"] is True
        refused = await c.post(
            f"{base}/{ids['question']}/answer", json={"text": "我替他答"}, headers=ADMIN
        )
        assert refused.status_code == 409

        c.cookies.set(WEB_SID, "member")
        mine = (await c.get("/api/web/v1/knowbot-questions")).json()["questions"]
        assert sorted(q["kind"] for q in mine) == ["ask_before", "question"]
        assert all("owner_subject" not in q and "route" not in q for q in mine)
        url = f"/api/web/v1/knowbot-questions/{ids['question']}/answer"
        assert (await c.post(url, json={"text": "按营业收入"})).status_code == 403
        answered = await c.post(url, json={"text": "按营业收入"}, headers=WEB)
        assert answered.status_code == 200 and answered.json()["sent"] is True

        detail = (await c.get(f"/api/admin/v1/knowbot-requests/{request.id}")).json()[
            "answered"
        ]
        assert {(a["kind"], a["answered_by"]) for a in detail} == {
            ("tool_approval", "operator"),
            ("question", "user"),
        }


@pytest.mark.asyncio
async def test_amendments_through_the_api(wired) -> None:
    """补充方法（SDD 0035）。

    用户只给自己的写；admin 看得到、能不照做；核对出处前挡住。
    """
    svc, *_ = wired
    from app.domain.knowbots.drafts import KnowbotRequestIn

    mine = await svc.submit("user-123", KnowbotRequestIn.model_validate(request_body()))
    theirs = await svc.submit(
        "someone-else", KnowbotRequestIn.model_validate(request_body())
    )
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        url = f"/api/web/v1/knowbot-requests/{mine.id}/amendments"
        no_csrf = await c.post(url, json={"text": "第 6 步改成对照管理层讨论"})
        assert no_csrf.status_code == 403
        made = await c.post(
            url, json={"text": "第 6 步改成对照管理层讨论"}, headers=WEB
        )
        assert made.status_code == 201
        amendment_id = made.json()["amendment_id"]
        other = await c.post(
            f"/api/web/v1/knowbot-requests/{theirs.id}/amendments",
            json={"text": "改别人的"},
            headers=WEB,
        )
        assert other.status_code == 404
        empty = await c.post(url, json={"text": ""}, headers=WEB)
        assert empty.status_code in {400, 422}
        [shown] = (await c.get("/api/web/v1/knowbot-requests")).json()["requests"]
        assert shown["amendments"] == [
            {
                "amendment_id": amendment_id,
                "text": "第 6 步改成对照管理层讨论",
                "created_at": shown["amendments"][0]["created_at"],
                "status": "open",
                "used_in_revision": None,
                "declined_reason": None,
                "declined_at": None,
            }
        ]

        draft = await svc.add_draft(str(mine.id), manifest())
        c.cookies.set(ADMIN_SID, "admin")
        [card] = [
            d
            for d in (await c.get("/api/admin/v1/knowbot-drafts")).json()["drafts"]
            if d["draft_id"] == str(draft.id)
        ]
        assert (card["amendments_open"], card["amendments_newer"]) == (
            [amendment_id],
            0,
        )
        review = await c.post(
            f"/api/admin/v1/knowbot-drafts/{draft.id}/rules-review", headers=ADMIN
        )
        assert review.status_code == 409
        assert review.json()["code"] == "amendments_open"

        decline = f"/api/admin/v1/knowbot-amendments/{amendment_id}/decline"
        c.cookies.set(ADMIN_SID, "operator")
        assert (
            await c.post(decline, json={"reason": "这一版先不做"}, headers=ADMIN)
        ).status_code == 403
        c.cookies.set(ADMIN_SID, "admin")
        no_reason = await c.post(decline, json={"reason": ""}, headers=ADMIN)
        assert no_reason.status_code in {400, 422}
        done = await c.post(decline, json={"reason": "这一版先不做"}, headers=ADMIN)
        assert done.status_code == 204
        detail = await c.get(f"/api/admin/v1/knowbot-requests/{mine.id}")
        [seen] = detail.json()["amendments"]
        assert (seen["status"], seen["declined_reason"]) == ("declined", "这一版先不做")
        assert seen["declined_by"] == "user-123"  # 假会话里 admin 的 subject
        review = await c.post(
            f"/api/admin/v1/knowbot-drafts/{draft.id}/rules-review", headers=ADMIN
        )
        assert review.status_code == 204

        c.cookies.set(WEB_SID, "member")
        [shown] = (await c.get("/api/web/v1/knowbot-requests")).json()["requests"]
        assert shown["amendments"][0]["status"] == "declined"
        assert "declined_by" not in shown["amendments"][0]
