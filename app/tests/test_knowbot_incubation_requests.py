"""knowbench 替用户提的孵化请求（请专家方案设计第 4.9 节第 6 条、
专家孵化方案第十三节）：以用户
的名义建「问题」申请（自己来、只有他看得到、不花钱），记来源，
参考回答只算提议并随 AI 一轮
交过去；同一个钥匙只建一份；没补用例不能测；按关系认调用方。"""

from __future__ import annotations

import httpx

from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.method_demand import FormalHandoff
from app.infrastructure.security.relation_auth import (
    ServiceCaller,
    relation_bindings,
    require_incubation_request_service,
)
from app.interfaces.http.internal import incubation_requests
from app.main import app
from tests.knowbot_fakes import OWNER
from tests.test_formal_method_service import Repository, Runtime

QUESTION = "每季度按固定口径对比几家电池公司的毛利率并预测下季"


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
    service = KnowbotCreationService(Repository(), Runtime())
    app.dependency_overrides[incubation_requests.service] = lambda: service
    app.dependency_overrides[require_incubation_request_service] = lambda: (
        ServiceCaller(caller="knowbench", principal=None)  # type: ignore[arg-type]
    )
    url = "/api/internal/v1/knowlab/incubation-requests"
    from uuid import uuid4

    payload = FormalHandoff(
        contract="knowlab/formal-handoff/v2",
        owner_subject=OWNER,
        question=QUESTION,
        project_id=str(uuid4()),
        task_id=str(uuid4()),
        key="knowbench:receipt:one",
        gap="核对口径",
        review_digest="sha256:" + "a" * 64,
    ).model_dump(mode="json")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as c:
            old = await c.post(
                url, json={**payload, "contract": "knowlab/formal-handoff/v1"}
            )
            assert old.status_code == 422
            assert not service.repo.requests
            first = await c.post(url, json=payload)
            assert (first.status_code, first.json()["created"]) == (201, True)
            again = await c.post(url, json=payload)
            assert again.json()["studio_method_id"] == first.json()["studio_method_id"]
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
