"""创意到专家的接口（SDD 0037）：会话与权限、所有者隔离、CSRF、两端的栏与详情页、
用户看的不带内部字段。"""

from __future__ import annotations

import httpx
import pytest

import app.interfaces.http.middleware.auth as auth_middleware
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_experts import KnowbotExpertService
from app.application.services.knowbot_questions import KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.test_run import CONTRACT, CaseOutputsV1
from app.interfaces.endpoints import knowbot_routes
from app.main import app
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryDataStore,
    MemoryKnowlab,
    accepted_request,
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
ALL_CLEAR = {
    "checks": {c: {"ok": True, "note": ""} for c in ("C-L5", "C-L2", "C-L1")},
    "accepted": True,
}
START = {"budget_limit": "20", "timeout_seconds": 600}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    repo, runtime, knowmart = MemoryKnowlab(), FakeRuntime(), FakeKnowmart()
    asks = KnowbotQuestionService(repo, runtime)
    svc = KnowbotCreationService(repo, runtime, on_refresh=asks.resend)
    experts = KnowbotExpertService(repo, knowmart)
    web = FakeAuthService({"member": session("web", "profile:read")})
    admin = FakeAuthService(
        {
            "admin": session("admin", "knowlab:admin", "knowlab:release"),
            # 只有管理端基本权限、没有 knowlab:release 的人。所有者 2026-10-23 起
            # operator 角色也换算出 knowlab:release（见 auth.OPERATOR_SCOPES）；
            # 以后 admin 多出的权限定了，operator 会落回这里
            "base": session("admin", "knowlab:admin"),
            "no-scope": session("admin", "profile:read"),
        }
    )
    monkeypatch.setattr(auth_middleware, "web_auth_service", web)
    monkeypatch.setattr(auth_middleware, "admin_auth_service", admin)
    monkeypatch.setattr(knowbot_routes, "runtime_clients", lambda: runtime)
    app.dependency_overrides[knowbot_routes.service] = lambda: svc
    app.dependency_overrides[knowbot_routes.expert_service] = lambda: experts
    app.dependency_overrides[knowbot_routes.board_service] = lambda: KnowbotBoard(repo)
    data = KnowbotDataService(repo, MemoryDataStore())
    app.dependency_overrides[knowbot_routes.data_service] = lambda: data
    app.dependency_overrides[knowbot_routes.question_service] = lambda: asks
    yield svc, repo, runtime, knowmart
    app.dependency_overrides.clear()


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _testing(svc: KnowbotCreationService, owner: str = OWNER):
    request = await accepted_request(svc, owner)
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    return request, draft, test


@pytest.mark.asyncio
async def test_web_needs_a_session_and_csrf(wired) -> None:
    async with client() as c:
        assert (
            await c.get("/api/web/v1/knowbot-board/applications")
        ).status_code == 401
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
        assert ok.status_code == 201 and ok.json()["version"] == 1
        assert ok.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_admin_routes_need_the_admin_scope_and_not_a_web_session(wired) -> None:
    board = "/api/admin/v1/knowbot-board/acceptance"
    async with client() as c:
        assert (await c.get(board)).status_code == 401
        c.cookies.set(WEB_SID, "member")
        assert (await c.get(board)).status_code == 401
        c.cookies.set(ADMIN_SID, "no-scope")
        assert (await c.get(board)).status_code == 403
        c.cookies.set(ADMIN_SID, "admin")
        assert (await c.get(board)).status_code == 200
        bad = await c.get("/api/admin/v1/knowbot-board/nope")
        assert bad.status_code in {400, 422}


@pytest.mark.asyncio
async def test_base_scope_cannot_accept_incubate_deliver_or_reset(wired) -> None:
    """只有基本权限的能看、发起测试、刷新、答跑法问题；受理、孵化、交付、判定卡住
    要多一项 knowlab:release。"""
    svc, repo, *_ = wired
    request, draft, test = await _testing(svc)
    async with client() as c:
        c.cookies.set(ADMIN_SID, "base")
        assert (await c.get("/api/admin/v1/knowbot-counts")).status_code == 200
        req = f"/api/admin/v1/knowbot-requests/{request.id}"
        drafts = f"/api/admin/v1/knowbot-drafts/{draft.id}"
        expert = "/api/admin/v1/knowbot-experts/00000000-0000-4000-8000-000000000000"
        for path, body in [
            (f"{req}/versions/1/acceptance", ALL_CLEAR),
            (f"{req}/drafts", {"manifest": manifest()}),
            (f"{drafts}/rules-review", None),
            (f"{drafts}/review-request", None),
            (f"/api/admin/v1/knowbot-tests/{test.id}/reset", None),
            (f"{expert}/return", None),
            (f"{expert}/review", {"approved": True, "listing": "self"}),
            (f"{expert}/deliver", None),
            (f"{expert}/stop", {"reason": "x"}),
        ]:
            refused = await c.post(path, json=body, headers=ADMIN)
            assert refused.status_code == 403, path
        refreshed = await c.post(
            f"/api/admin/v1/knowbot-tests/{test.id}/refresh", headers=ADMIN
        )
        assert refreshed.status_code == 200, refreshed.text


@pytest.mark.asyncio
async def test_the_whole_life_through_the_api(wired) -> None:
    svc, repo, runtime, knowmart = wired
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        made = await c.post(
            "/api/web/v1/knowbot-requests", json=request_body(), headers=WEB
        )
        request_id = made.json()["request_id"]

        # 受理
        c.cookies.set(ADMIN_SID, "admin")
        assert (await c.get("/api/admin/v1/knowbot-counts")).json()["counts"][
            "acceptance"
        ] == 1
        [pending] = (await c.get("/api/admin/v1/knowbot-board/acceptance")).json()[
            "items"
        ]
        assert (pending["category"], pending["version"]) == ("pending", 1)
        assert pending["idea"]["kind"] == "six_questions"
        url = f"/api/admin/v1/knowbot-requests/{request_id}/versions/1/acceptance"
        half = {**ALL_CLEAR, "accepted": False}
        assert (await c.post(url, json=half, headers=ADMIN)).status_code == 422
        accepted = await c.post(url, json=ALL_CLEAR, headers=ADMIN)
        assert accepted.status_code == 201
        assert (await c.post(url, json=ALL_CLEAR, headers=ADMIN)).status_code == 409

        # 孵化
        [todo] = (await c.get("/api/admin/v1/knowbot-board/incubation")).json()["items"]
        assert (todo["category"], todo["latest_accepted"]) == ("todo", 1)
        draft = await c.post(
            f"/api/admin/v1/knowbot-requests/{request_id}/drafts",
            json={"manifest": manifest()},
            headers=ADMIN,
        )
        assert draft.status_code == 201
        assert (draft.json()["version"], draft.json()["revision"]) == ("1", 1)
        draft_id = draft.json()["draft_id"]
        base = f"/api/admin/v1/knowbot-drafts/{draft_id}"
        asked = await c.post(f"{base}/review-request", headers=ADMIN)
        assert asked.status_code == 204

        # 用户审核准专家（审核栏里一条待办）
        assert (await c.get("/api/web/v1/knowbot-counts")).json()["inbox"] == 1
        [item] = (await c.get("/api/web/v1/knowbot-board/inbox")).json()["items"]
        assert (item["state"], item["type"], item["label"]) == ("todo", "draft", "1.1")
        assert item["rule_sources"] == [
            {"rule": r["rule"], "ref": r["ref"]} for r in manifest()["rule_sources"]
        ]
        review = f"/api/web/v1/knowbot-drafts/{draft_id}/review"
        assert (await c.post(review, json={"ok": True})).status_code == 403
        assert (await c.post(review, json={"ok": True}, headers=WEB)).status_code == 200

        # 测试
        early = await c.post(f"{base}/test", json=START, headers=ADMIN)
        assert early.status_code == 409 and early.json()["code"] == "rules_not_reviewed"
        assert (await c.post(f"{base}/rules-review", headers=ADMIN)).status_code == 204
        [to_start] = (await c.get("/api/admin/v1/knowbot-board/tests")).json()["items"]
        assert to_start["category"] == "to_start"
        assert to_start["draft"]["rules_reviewed_by"] == OWNER
        assert to_start["draft"]["blocked"] is None
        started = await c.post(f"{base}/test", json=START, headers=ADMIN)
        assert started.status_code == 201
        test = started.json()["test"]
        assert (test["number"], test["status"]) == (1, "queued")
        runtime.results[test["run_id"]] = result(test["run_id"], "passed")
        refreshed = await c.post(
            f"/api/admin/v1/knowbot-tests/{test['test_id']}/refresh", headers=ADMIN
        )
        assert refreshed.json()["test"]["status"] == "passed"

        # 交付：交回 → 用户同意 → 运营审核 → 交 knowmart
        [expert] = (await c.get("/api/admin/v1/knowbot-board/delivery")).json()["items"]
        assert (expert["category"], expert["version"], expert["test_number"]) == (
            "to_return",
            1,
            1,
        )
        assert (await c.get("/api/web/v1/knowbot-board/experts")).json()["items"] == []
        ebase = f"/api/admin/v1/knowbot-experts/{expert['expert_id']}"
        assert (await c.post(f"{ebase}/return", headers=ADMIN)).status_code == 200
        assert (await c.get("/api/web/v1/knowbot-counts")).json() == {
            "contract_version": 1,
            "inbox": 1,
            "experts_new": 1,
        }
        decision = f"/api/web/v1/knowbot-experts/{expert['expert_id']}/decision"
        decided = await c.post(decision, json={"accept": True}, headers=WEB)
        assert decided.json()["stage"] == "to_review"
        reviewed = await c.post(
            f"{ebase}/review",
            json={"approved": True, "listing": "self"},
            headers=ADMIN,
        )
        assert reviewed.json()["stage"] == "listed", reviewed.text
        assert knowmart.delivered[0]["manifest"] == manifest()
        [mine] = (await c.get("/api/web/v1/knowbot-board/experts")).json()["items"]
        assert (mine["category"], mine["knowbot_id"]) == ("usable", "kb-1")

        # 用户看的：没有清单、规则名、run、谁做的
        detail = (await c.get(f"/api/web/v1/knowbot-requests/{request_id}")).json()
        assert detail["summary"] == {
            "version": 1,
            "acceptance": "accepted",
            "draft": "1.1",
            "test": {"number": 1, "status": "passed"},
            "expert": {"version": 1, "stage": "listed"},
        }
        assert detail["versions"][0]["acceptance"]["accepted"] is True
        rendered = str(detail)
        for hidden in ("manifest", "run_id", "owner_subject", "'checks'", "'by'"):
            assert hidden not in rendered, hidden
        kinds = [e["kind"] for e in detail["timeline"]]
        assert kinds[0] == "version" and kinds[-1] == "listed"

        # 停用：用户自己停，knowmart 那一版跟着停
        stop = f"/api/web/v1/knowbot-experts/{expert['expert_id']}/stop"
        stopped = await c.post(stop, json={"reason": "不用了"}, headers=WEB)
        assert stopped.json()["stage"] == "stopped"
        assert knowmart.stopped == [
            {"owner_subject": OWNER, "knowbot_id": "kb-1", "version": "1"}
        ]


@pytest.mark.asyncio
async def test_revising_through_the_api_adds_a_version(wired) -> None:
    svc, repo, *_ = wired
    request = await accepted_request(svc)
    body = request_body()
    body["idea"]["answers"]["steps"] = "先算毛利率，再算净利率"
    revision = {k: body[k] for k in ("idea", "cases", "consent_test_data")}
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        url = f"/api/web/v1/knowbot-requests/{request.id}/versions"
        assert (await c.post(url, json=revision)).status_code == 403
        made = await c.post(url, json=revision, headers=WEB)
        assert made.status_code == 201 and made.json()["version"] == 2
        renamed = await c.post(url, json=revision | {"title": "别的"}, headers=WEB)
        assert renamed.status_code in {400, 422}  # 名称一生不变
        detail = (await c.get(f"/api/web/v1/knowbot-requests/{request.id}")).json()
        assert [v["version"] for v in detail["versions"]] == [1, 2]
        assert detail["versions"][1]["acceptance"] is None
        c.cookies.set(ADMIN_SID, "admin")
        items = (await c.get("/api/admin/v1/knowbot-board/acceptance")).json()["items"]
        assert [(i["category"], i["version"]) for i in items] == [
            ("pending", 2),
            ("accepted", 1),
        ]


@pytest.mark.asyncio
async def test_admin_sees_how_far_a_running_test_got_and_users_do_not(wired) -> None:
    """测试中看进度（待办账 135）：管理端测试上带进度，跑完就没有；用户的结果不变。"""
    svc, _, runtime, _ = wired
    request, _, test = await _testing(svc)
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
    runtime.results[test.run_id] = result(test.run_id, "running", progress=progress)
    await svc.refresh(str(test.id))
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        [shown] = (await c.get("/api/admin/v1/knowbot-board/tests")).json()["items"]
        assert shown["category"] == "running"
        assert shown["test"]["progress"] == progress | {"queued_behind": None}
        assert shown["test"]["run_status"] == "running"
        c.cookies.set(WEB_SID, "member")
        mine = (await c.get(f"/api/web/v1/knowbot-requests/{request.id}")).json()
        assert "progress" not in str(mine)
        runtime.results[test.run_id] = result(test.run_id, "passed")
        await svc.refresh(str(test.id))
        [done] = (await c.get("/api/admin/v1/knowbot-board/tests")).json()["items"]
        assert done["category"] == "finished"
        assert done["test"]["progress"] is None and done["test"]["run_status"] is None


@pytest.mark.asyncio
async def test_admin_sees_a_test_that_was_accepted_but_has_not_started(wired) -> None:
    """运行方受理了还没开跑：管理端拿到「已受理」、没有开跑时间（账 136 第 ②）；
    排在测试账号别的委托后面时，进度里带那件委托的编号（0034 §三.3）。"""
    svc, _, runtime, _ = wired
    _, _, test = await _testing(svc)
    runtime.results[test.run_id] = result(
        test.run_id, "accepted", blocked="computer_offline"
    )
    await svc.refresh(str(test.id))
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        [shown] = (await c.get("/api/admin/v1/knowbot-board/tests")).json()["items"]
        assert shown["category"] == "queued"
        assert shown["test"]["run_status"] == "accepted"
        assert shown["test"]["run_running_since"] is None
        # 账 141：已受理没开跑时，写明在等什么
        assert shown["test"]["run_blocked"] == "computer_offline"
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
        runtime.results[test.run_id] = result(
            test.run_id,
            "running",
            started_at="2026-10-19T00:49:14Z",
            progress=queued,
        )
        await svc.refresh(str(test.id))
        [shown] = (await c.get("/api/admin/v1/knowbot-board/tests")).json()["items"]
        assert shown["test"]["run_running_since"].startswith("2026-10-19T00:49:14")
        assert shown["test"]["progress"]["queued_behind"] == behind
        assert shown["test"]["run_blocked"] is None


@pytest.mark.asyncio
async def test_users_only_reach_their_own(wired) -> None:
    svc, *_ = wired
    other, theirs, test = await _testing(svc, "someone-else")
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        assert (await c.get("/api/web/v1/knowbot-board/applications")).json()[
            "items"
        ] == []
        for path in (
            f"/api/web/v1/knowbot-requests/{other.id}",
            f"/api/web/v1/knowbot-tests/{test.id}/outputs",
            "/api/web/v1/knowbot-requests/not-a-uuid",
        ):
            assert (await c.get(path)).status_code == 404, path
        review = await c.post(
            f"/api/web/v1/knowbot-drafts/{theirs.id}/review",
            json={"ok": True},
            headers=WEB,
        )
        assert review.status_code == 404


@pytest.mark.asyncio
async def test_unknown_ids_are_404(wired) -> None:
    async with client() as c:
        c.cookies.set(ADMIN_SID, "admin")
        missing = await c.post(
            "/api/admin/v1/knowbot-tests/not-a-uuid/refresh", headers=ADMIN
        )
        assert missing.status_code == 404
        assert (await c.get("/api/admin/v1/knowbot-requests/x")).status_code == 404
        gone = await c.post(
            "/api/admin/v1/knowbot-experts/not-a-uuid/return", headers=ADMIN
        )
        assert gone.status_code == 404


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
    svc, _, runtime, _ = wired
    request, draft, test = await _testing(svc)
    runtime.results[test.run_id] = waiting(test.run_id)
    await svc.refresh(str(test.id))
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
        # 带着是哪位准专家的（自己的），web端按它对上（SDD 0037）
        assert {q["draft_id"] for q in mine} == {str(draft.id)}
        assert {q["label"] for q in mine} == {"1.1"}
        inbox = (await c.get("/api/web/v1/knowbot-board/inbox")).json()
        assert sorted(i["type"] for i in inbox["items"]) == ["question", "question"]
        assert sorted(i["kind"] for i in inbox["items"]) == ["ask_before", "question"]
        url = f"/api/web/v1/knowbot-questions/{ids['question']}/answer"
        assert (await c.post(url, json={"text": "按营业收入"})).status_code == 403
        answered = await c.post(url, json={"text": "按营业收入"}, headers=WEB)
        assert answered.status_code == 200 and answered.json()["sent"] is True
        inbox = (await c.get("/api/web/v1/knowbot-board/inbox")).json()
        assert [a["answered_by"] for a in inbox["answered"]] == ["user"]

        c.cookies.set(ADMIN_SID, "admin")
        detail = (await c.get(f"/api/admin/v1/knowbot-requests/{request.id}")).json()[
            "answered"
        ]
        assert {(a["kind"], a["answered_by"]) for a in detail} == {
            ("tool_approval", "operator"),
            ("question", "user"),
        }


@pytest.mark.asyncio
async def test_outputs_through_the_api(wired) -> None:
    """SDD 0037：web 只看自己那次测试的产出；管理端看全部（验证这一稿）。"""
    svc, repo, runtime, _ = wired
    request, draft, test = await _testing(svc)
    runtime.case_outputs[(test.run_id, "c1")] = CaseOutputsV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": test.run_id,
            "case_id": "c1",
            "deliverable": {"path": "底稿/成稿.md", "text": "# 成稿"},
        }
    )
    runtime.results[test.run_id] = result(test.run_id, "passed")
    await svc.refresh(str(test.id))
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        url = f"/api/web/v1/knowbot-tests/{test.id}/outputs"
        got = (await c.get(url)).json()
        assert got["cases"][0]["outputs"]["deliverable"]["text"] == "# 成稿"
        assert got["consent_needed"] is False and "run_id" not in got
        # 老创意（旧说明下同意的）：不显示，同意新说明后才显示（审读 10-19）
        await repo.set_consent_version(request.id, 1)
        hidden = (await c.get(url)).json()
        assert hidden["consent_needed"] is True and hidden["cases"] == []
        consent = f"/api/web/v1/knowbot-requests/{request.id}/consent"
        assert (await c.post(consent)).status_code == 403  # 没带 CSRF
        assert (await c.post(consent, headers=WEB)).status_code == 204
        assert (await c.get(url)).json()["consent_needed"] is False

        c.cookies.set(ADMIN_SID, "base")  # 只有基本权限也能看产出
        admin = await c.get(f"/api/admin/v1/knowbot-tests/{test.id}/outputs")
        assert admin.json()["cases"][0]["case_id"] == "c1"
        doc = await c.get(f"/api/admin/v1/knowbot-drafts/{draft.id}/manifest")
        assert doc.json()["manifest"] == manifest()


@pytest.mark.asyncio
async def test_old_amendments_are_read_only_in_the_detail(wired) -> None:
    """补充方法已取消（SDD 0037 第三节）：旧的照旧列在详情页，没有写的接口。"""
    svc, repo, *_ = wired
    request = await accepted_request(svc)
    await repo.create_amendment(
        request_id=request.id, owner_subject=OWNER, text="第 6 步改成对照管理层讨论"
    )
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        detail = (await c.get(f"/api/web/v1/knowbot-requests/{request.id}")).json()
        assert [a["status"] for a in detail["amendments"]] == ["open"]
        gone = await c.post(
            f"/api/web/v1/knowbot-requests/{request.id}/amendments",
            json={"text": "再补一条"},
            headers=WEB,
        )
        assert gone.status_code in {404, 405}
