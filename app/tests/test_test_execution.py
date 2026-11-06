import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.application.errors.exceptions import (
    BadGatewayError,
    BadRequestError,
    NotFoundError,
)
from app.domain.knowbots.test_execution import render
from app.infrastructure.external.knowbot_clients import RuntimeTestClients
from tests.knowbot_fakes import OWNER
from tests.test_knowbot_clients import KNOWBENCH, FakeOidc
from tests.test_knowbot_test_outputs import _testing
from tests.test_knowbot_test_outputs import parts as parts


def test_provider_schema_matches_model():
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts/knowbot-test-execution/v1/execution.schema.json"
    )
    assert path.read_text() == render()


async def test_only_author_can_read_live_test_and_consent_precedes_runtime(parts):
    svc, repo, runtime = parts
    request, _, test = await _testing(svc)
    with pytest.raises(NotFoundError):
        await svc.my_test_execution("another-user", str(test.id), None)
    assert runtime.execution_asked == []
    view = await svc.my_test_execution(OWNER, str(test.id), "c1")
    assert view["run_id"] == test.run_id
    assert runtime.execution_asked == [(test.run_id, OWNER, "c1")]
    repo.requests[request.id] = replace(request, consent_version=0)
    with pytest.raises(BadRequestError):
        await svc.my_test_execution(OWNER, str(test.id), None)
    assert len(runtime.execution_asked) == 1


@pytest.mark.parametrize("different", [False, True])
async def test_service_client_checks_run_and_forwards_verified_owner(different):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "contract": "knowbot-test-execution/v1",
                "run_id": "wrong-run" if different else "knowlab-run-0001",
                "status": "running",
                "cases": [],
            },
        )

    client = RuntimeTestClients(
        KNOWBENCH, oidc_client=FakeOidc(), transport=httpx.MockTransport(handler)
    )
    if different:
        with pytest.raises(BadGatewayError):
            await client.execution("knowbench", "knowlab-run-0001", OWNER, "c1")
    else:
        await client.execution("knowbench", "knowlab-run-0001", OWNER, "c1")
    assert seen[0].url.params["owner_subject"] == OWNER
    assert seen[0].url.params["case_id"] == "c1"
    assert "method_text" not in json.dumps(seen[0].url.params.multi_items())


async def test_author_can_stop_only_own_test_and_repeated_stop_is_idempotent(parts):
    svc, repo, runtime = parts
    _, _, test = await _testing(svc)
    with pytest.raises(NotFoundError):
        await svc.stop_mine("another-user", str(test.id))
    assert runtime.cancelled == []
    stopped = await svc.stop_mine(OWNER, str(test.id))
    assert stopped.report["status"] == "cancelled"
    assert runtime.cancelled == [test.run_id]
    assert (await svc.stop_mine(OWNER, str(test.id))).id == test.id
    assert runtime.cancelled == [test.run_id]


async def test_failed_runtime_stop_does_not_pretend_test_stopped(parts):
    svc, repo, runtime = parts
    _, _, test = await _testing(svc)
    runtime.can_cancel = False
    with pytest.raises(BadGatewayError):
        await svc.stop_mine(OWNER, str(test.id))
    assert repo.tests[test.id].status == test.status
