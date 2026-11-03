"""Demand-side suggestions survive incubation without becoming author consent."""

import json
from pathlib import Path

import httpx
import pytest

from app.application.errors.exceptions import ConcurrencyConflictError
from app.domain.knowbots.method_demand import RuntimeIncubationV2, render
from tests.knowbot_fakes import OWNER, own_computer
from tests.test_knowbot_incubation_requests import QUESTION, body
from tests.test_knowbot_incubation_service import Wired


def request_doc():
    return {
        **body().model_dump(mode="json"),
        "contract": "knowlab/runtime-incubation/v2",
        "method_demand": {
            "match_artifact_version": 1,
            "question_revision": 1,
            "outcome": "none",
            "requirements": [
                {
                    "id": "r1",
                    "kind": "result",
                    "statement": "预测下季",
                    "question_quote": "预测下季",
                    "required": True,
                    "origin": "user_explicit",
                    "rationale": "用户要求预测",
                }
            ],
            "method_proposals": [
                {
                    "id": "m1",
                    "requirement_ids": ["r1"],
                    "approach": "根据同季历史数据预测",
                    "reason": "供作者比较的建议",
                    "data_requirements": ["历史季度序列"],
                }
            ],
            "gaps": [
                {
                    "requirement_id": "r1",
                    "status": "uncovered",
                    "reason": "未找到支持预测的专家",
                }
            ],
            "clarification": None,
        },
    }


async def test_context_is_retained_as_reference_not_accepted_method():
    w = Wired()
    own_computer(w.repo)
    command = RuntimeIncubationV2.model_validate(request_doc())
    found, created = await w.svc.submit_from_runtime(
        OWNER, command, runtime="knowbench"
    )
    assert created and found.idea == {"kind": "question", "question": QUESTION}
    assert found.origin["method_demand"] == command.method_demand.model_dump(
        mode="json"
    )
    assert "尚未经作者接受" in found.reference_answer
    again, created = await w.svc.submit_from_runtime(
        OWNER, command, runtime="knowbench"
    )
    assert not created and again.id == found.id
    changed = request_doc()
    changed["method_demand"]["method_proposals"][0]["approach"] = "新的建议"
    with pytest.raises(ConcurrencyConflictError, match="same key"):
        await w.svc.submit_from_runtime(
            OWNER, RuntimeIncubationV2.model_validate(changed), runtime="knowbench"
        )
    await w.inc.start(OWNER, str(found.id), action="draft")
    sent = w.incubator.started[0]
    assert sent.seed.kind == "question" and sent.seed.text == QUESTION
    assert "供作者比较的建议" in sent.reference
    assert not sent.workdraft.body.steps


@pytest.mark.parametrize(
    "damage",
    ["quote", "unknown_goal", "duplicate_goal", "optional_method", "too_large"],
)
def test_invalid_or_truncated_demand_is_not_accepted(damage):
    doc = request_doc()
    demand = doc["method_demand"]
    if damage == "quote":
        demand["requirements"][0]["question_quote"] = "用户没有说的话"
    elif damage == "unknown_goal":
        demand["method_proposals"][0]["requirement_ids"] = ["invented"]
    elif damage == "duplicate_goal":
        demand["requirements"].append(demand["requirements"][0])
    elif damage == "optional_method":
        demand["requirements"][0].update(kind="explicit_method", required=False)
    else:
        doc["reference_answer"] = "文" * 20000
    with pytest.raises(ValueError):
        RuntimeIncubationV2.model_validate(doc)


def test_provider_schema_is_generated_from_the_runtime_contract():
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts/runtime-incubation/v2/request.schema.json"
    )
    assert path.read_text() == render()
    import jsonschema

    jsonschema.validate(request_doc(), json.loads(path.read_text()))


async def test_internal_route_preserves_v2_and_rejects_invalid_demand():
    from app.infrastructure.security.relation_auth import (
        ServiceCaller,
        require_incubation_request_service,
    )
    from app.interfaces.http.internal import incubation_requests
    from app.main import app

    w = Wired()
    app.dependency_overrides[incubation_requests.service] = lambda: w.svc
    app.dependency_overrides[require_incubation_request_service] = lambda: (
        ServiceCaller(caller="knowbench", principal=None)
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            url = "/api/internal/v1/knowlab/incubation-requests"
            first = await client.post(url, json=request_doc())
            assert first.status_code == 201
            assert first.json()["contract_version"] == 2
            replay = await client.post(url, json=request_doc())
            assert replay.status_code == 200
            assert replay.json()["request_id"] == first.json()["request_id"]
            invalid = request_doc()
            invalid["method_demand"]["requirements"][0]["question_quote"] = "编造"
            assert (await client.post(url, json=invalid)).status_code == 422
    finally:
        app.dependency_overrides.clear()
