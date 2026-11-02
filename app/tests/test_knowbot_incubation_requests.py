"""knowbench 替用户提的孵化请求（请专家方案设计第 4.9 节第 6 条、
专家孵化方案第十三节）：以用户
的名义建「问题」申请（自己来、只有他看得到、不花钱），记来源，
参考回答只算提议并随 AI 一轮
交过去；同一个钥匙只建一份；没补用例不能测；按关系认调用方。"""

from __future__ import annotations

import httpx
import pytest

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.services.knowbot_board import KnowbotBoard
from app.domain.knowbots.drafts import RuntimeIncubationIn
from app.infrastructure.security.relation_auth import (
    ServiceCaller,
    relation_bindings,
    require_incubation_request_service,
)
from app.interfaces.http.internal import incubation_requests
from app.main import app
from tests.knowbot_fakes import OWNER, manifest, own_computer
from tests.test_knowbot_incubation_service import Wired

QUESTION = "每季度按固定口径对比几家电池公司的毛利率并预测下季"
ANSWER = "（模型直接答的）宁德时代毛利率约 22%……"


def body(**over) -> RuntimeIncubationIn:  # type: ignore[no-untyped-def]
    return RuntimeIncubationIn.model_validate(
        {
            "owner_subject": OWNER,
            "question": QUESTION,
            "project_id": "p-1",
            "task_id": "t-1",
            "key": "knowbench:find:0001",
            "reference_answer": ANSWER,
        }
        | over
    )


async def test_a_question_request_is_made_once_for_the_user_only() -> None:
    w = Wired()
    found, created = await w.svc.submit_from_runtime(OWNER, body(), runtime="knowbench")
    assert created
    assert (found.idea, found.cases, found.consent_version) == (
        {"kind": "question", "question": QUESTION},
        [],
        0,
    )
    assert found.title == QUESTION[:80]
    assert found.incubation_mode == "self"
    assert w.repo.acceptances[(found.id, 1)].kind == "self"  # 自己来、不过平台
    assert found.origin == {
        "app": "knowbench",
        "project_id": "p-1",
        "task_id": "t-1",
        "key": "knowbench:find:0001",
    }
    assert found.reference_answer == ANSWER
    again, created = await w.svc.submit_from_runtime(OWNER, body(), runtime="knowbench")
    assert (again.id, created) == (found.id, False)  # 同一个钥匙只建一份
    from app.application.errors.exceptions import BadRequestError

    with pytest.raises(BadRequestError) as empty:
        await w.svc.submit_from_runtime(
            OWNER, body(question="   "), runtime="knowbench"
        )
    assert empty.value.code == "question_empty"
    # 运营看不到（自己来的名称）
    assert await KnowbotBoard(w.repo).lives(operator=True) == []


async def test_the_reference_goes_with_the_ai_round_and_tests_need_cases() -> None:
    w = Wired()
    own_computer(w.repo)
    found, _ = await w.svc.submit_from_runtime(OWNER, body(), runtime="knowbench")
    await w.inc.start(OWNER, str(found.id), action="draft")
    sent = w.incubator.started[0]
    assert sent.seed.kind == "question" and sent.reference == ANSWER
    # 没补用例的那一版出的准专家不能测
    draft = await w.repo.create_draft(
        request_id=found.id,
        request_version=1,
        revision=1,
        version="1",
        manifest=manifest(),
        mode="self",
        rules_reviewed_by="system:workdraft",
    )
    with pytest.raises(ConcurrencyConflictError) as raised:
        await w.svc.start_self_test(
            OWNER,
            str(draft.id),
            environment_id="11111111-1111-4111-8111-111111111111",
            workspace_root="D:/work",
            budget_limit="5",
            timeout_seconds=600,
        )
    assert raised.value.code == "cases_missing"


async def test_without_a_reference_the_round_carries_none() -> None:
    w = Wired()
    own_computer(w.repo)
    found, _ = await w.svc.submit_from_runtime(
        OWNER,
        body(reference_answer=None, key="knowbench:find:0002"),
        runtime="knowbench",
    )
    await w.inc.start(OWNER, str(found.id), action="draft")
    assert w.incubator.started[0].reference is None
    assert "reference" not in w.incubator.started[0].model_dump(mode="json")


def test_only_knowbench_counts_as_a_caller() -> None:
    env = {
        "SERVICE_AUTH_INCUBATION_REQUEST_CALLERS": "knowbench,knowmart",
        "SERVICE_AUTH_INCUBATION_REQUEST_KNOWBENCH_AUDIENCE": "bench",
        "SERVICE_AUTH_INCUBATION_REQUEST_KNOWBENCH_SUBJECT_ALLOWLIST": "admin/b",
        "SERVICE_AUTH_INCUBATION_REQUEST_KNOWBENCH_REQUIRED_SCOPE": (
            "knowlab:incubation-request"
        ),
        "SERVICE_AUTH_INCUBATION_REQUEST_KNOWMART_AUDIENCE": "mart",
        "SERVICE_AUTH_INCUBATION_REQUEST_KNOWMART_SUBJECT_ALLOWLIST": "admin/m",
        "SERVICE_AUTH_INCUBATION_REQUEST_KNOWMART_REQUIRED_SCOPE": (
            "knowlab:incubation-request"
        ),
    }
    assert [b.caller for b in relation_bindings("incubation-request", env)] == [
        "knowbench"
    ]
    assert relation_bindings("incubation-request", {}) == []


async def test_the_internal_route_makes_201_then_200() -> None:
    w = Wired()
    app.dependency_overrides[incubation_requests.service] = lambda: w.svc
    app.dependency_overrides[require_incubation_request_service] = lambda: (
        ServiceCaller(caller="knowbench", principal=None)  # type: ignore[arg-type]
    )
    url = "/api/internal/v1/knowlab/incubation-requests"
    payload = body().model_dump(mode="json")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as c:
            first = await c.post(url, json=payload)
            assert (first.status_code, first.json()["created"]) == (201, True)
            again = await c.post(url, json=payload)
            assert (again.status_code, again.json()["request_id"]) == (
                200,
                first.json()["request_id"],
            )
            bad = await c.post(url, json={**payload, "question": ""})
            assert bad.status_code == 422
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            assert (await c.post(url, json=payload)).status_code == 404  # 公网来的
    finally:
        app.dependency_overrides.clear()
