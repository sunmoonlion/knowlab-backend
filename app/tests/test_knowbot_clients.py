"""出站客户端：运行方登记、令牌缓存、错误映射、契约核对（假 Casdoor 与假目标）。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.application.errors.exceptions import (
    BadGatewayError,
    BadRequestError,
    ServiceUnavailableError,
)
from app.domain.knowbots.test_run import CONTRACT, RunRequestV1
from app.infrastructure.external.knowbot_clients import (
    KnowmartDeliveryClient,
    RuntimeTestClients,
    registered_runtimes,
)
from app.infrastructure.external.service_client import service_relation
from tests.knowbot_fakes import manifest, request_body, result


def relation_env(target: str, relation: str, url: str) -> dict[str, str]:
    p = f"{target.upper()}_{relation.upper()}_SERVICE_"
    return {
        f"{target.upper()}_INTERNAL_URL": url,
        p + "CLIENT_ID": f"{target}-{relation}-client",
        p + "CLIENT_SECRET": "not-a-real-secret",
        p + "SCOPE": f"{target}:{relation}",
        p
        + "DISCOVERY_URL": "https://casdoor.example.test/.well-known/openid-configuration",
        p + "BACKCHANNEL_ENDPOINT": "https://casdoor-internal.example.test",
    }


KNOWBENCH = relation_env(
    "knowbench",
    "testrun",
    "https://knowbench-tls.ns.svc.cluster.local/api/internal/v1/knowbot-runtime",
)
KNOWMART = relation_env(
    "knowmart",
    "deliver",
    "https://knowmart-tls.ns.svc.cluster.local/api/internal/v1/knowmart",
)


class FakeOidc:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def exchange_client_credentials(self, **kw: str) -> dict:
        self.calls.append(kw)
        return {"access_token": f"token-{len(self.calls)}", "expires_in": 300}


def run_request() -> RunRequestV1:
    return RunRequestV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": "knowlab-run-0001",
            "owner_subject": "user-123",
            "manifest": manifest(),
            "cases": request_body()["cases"],
            "budget": {"limit": "20"},
            "timeout_seconds": 600,
        }
    )


def test_only_fully_configured_https_relations_count() -> None:
    assert registered_runtimes(KNOWBENCH) == frozenset({"knowbench"})
    assert registered_runtimes({}) == frozenset()
    partial = dict(KNOWBENCH)
    partial.pop("KNOWBENCH_TESTRUN_SERVICE_CLIENT_SECRET")
    assert registered_runtimes(partial) == frozenset()
    wrong_scope = KNOWBENCH | {"KNOWBENCH_TESTRUN_SERVICE_SCOPE": "knowbench:x"}
    assert registered_runtimes(wrong_scope) == frozenset()
    plain = KNOWBENCH | {"KNOWBENCH_INTERNAL_URL": "http://knowbench/api"}
    assert service_relation("knowbench", "testrun", plain) is None


@pytest.mark.asyncio
async def test_start_and_status_use_the_registered_runtime_with_a_cached_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                202, json=result(body["run_id"], "accepted").model_dump(mode="json")
            )
        return httpx.Response(
            200, json=result("knowlab-run-0001", "running").model_dump(mode="json")
        )

    oidc = FakeOidc()
    clients = RuntimeTestClients(
        KNOWBENCH, oidc_client=oidc, transport=httpx.MockTransport(handler)
    )
    started = await clients.start("knowbench", run_request())
    assert started.status == "accepted"
    assert (await clients.status("knowbench", "knowlab-run-0001")).status == "running"
    assert [str(r.url) for r in seen] == [
        "https://knowbench-tls.ns.svc.cluster.local/api/internal/v1/knowbot-runtime/test-runs",
        "https://knowbench-tls.ns.svc.cluster.local/api/internal/v1/knowbot-runtime/test-runs/knowlab-run-0001",
    ]
    assert all(r.headers["authorization"] == "Bearer token-1" for r in seen)
    assert oidc.calls == [
        {
            "scope": "knowbench:testrun",
            "client_id": "knowbench-testrun-client",
            "client_secret": "not-a-real-secret",
        }
    ]
    with pytest.raises(BadRequestError):
        await clients.start("nowhere", run_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error", "code"),
    [
        (httpx.Response(503), ServiceUnavailableError, "knowbench_unavailable"),
        (
            httpx.Response(409, json={"code": "run_conflict"}),
            BadGatewayError,
            "run_conflict",
        ),
        (
            httpx.Response(202, json={"status": "made-up"}),
            BadGatewayError,
            "test_run_contract_invalid",
        ),
        (
            httpx.Response(
                202, json=result("another-run-01", "accepted").model_dump(mode="json")
            ),
            BadGatewayError,
            "run_mismatch",
        ),
    ],
)
async def test_runtime_errors_are_mapped(response, error, code) -> None:
    clients = RuntimeTestClients(
        KNOWBENCH,
        oidc_client=FakeOidc(),
        transport=httpx.MockTransport(lambda _: response),
    )
    with pytest.raises(error) as raised:
        await clients.start("knowbench", run_request())
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_case_outputs_are_read_and_a_404_means_none() -> None:
    """SDD 0037：一条用例的产出；运行方说没有（它自己的 404）回 None。"""
    seen: list[str] = []
    answers = {
        "c1": httpx.Response(
            200,
            json={
                "contract": CONTRACT,
                "run_id": "knowlab-run-0001",
                "case_id": "c1",
                "deliverable": {"path": "底稿/成稿.md", "text": "# 成稿"},
                "steps": [{"step_id": "s1", "outcome": "not_reached"}],
            },
        ),
        "c2": httpx.Response(404, json={"code": "not_found"}),
        "c3": httpx.Response(404, json={"detail": "Not Found"}),
        "c4": httpx.Response(
            200,
            json={"contract": CONTRACT, "run_id": "knowlab-run-0001", "case_id": "c1"},
        ),
        "c5": httpx.Response(200, json={"contract": CONTRACT}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return answers[request.url.path.split("/")[-2]]

    clients = RuntimeTestClients(
        KNOWBENCH, oidc_client=FakeOidc(), transport=httpx.MockTransport(handler)
    )
    got = await clients.outputs("knowbench", "knowlab-run-0001", "c1")
    assert got is not None and got.deliverable and got.deliverable.text == "# 成稿"
    assert seen == [
        "https://knowbench-tls.ns.svc.cluster.local/api/internal/v1/knowbot-runtime"
        "/test-runs/knowlab-run-0001/cases/c1/outputs"
    ]
    assert await clients.outputs("knowbench", "knowlab-run-0001", "c2") is None
    # 旧版运行方没有这个接口：暂时取不到，不当成「没有」（审读 10-19）
    with pytest.raises(ServiceUnavailableError) as old:
        await clients.outputs("knowbench", "knowlab-run-0001", "c3")
    assert old.value.code == "runtime_outputs_unsupported"
    with pytest.raises(BadGatewayError) as other_case:
        await clients.outputs("knowbench", "knowlab-run-0001", "c4")
    assert other_case.value.code == "run_mismatch"
    with pytest.raises(BadGatewayError) as broken:
        await clients.outputs("knowbench", "knowlab-run-0001", "c5")
    assert broken.value.code == "test_run_contract_invalid"


@pytest.mark.asyncio
async def test_unreachable_runtime_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    clients = RuntimeTestClients(
        KNOWBENCH, oidc_client=FakeOidc(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ServiceUnavailableError):
        await clients.status("knowbench", "knowlab-run-0001")


@pytest.mark.asyncio
async def test_knowmart_delivery() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "contract_version": 1,
                "knowbot_id": "kb-1",
                "version": "1",
                "digest": "sha256:" + "b" * 64,
                "replayed": False,
            },
        )

    client = KnowmartDeliveryClient(
        KNOWMART, oidc_client=FakeOidc(), transport=httpx.MockTransport(handler)
    )
    delivered = await client.deliver(
        delivery_id="knowlab-draft-1",
        owner_subject="user-123",
        knowbot_ref="knowlab-r1",
        manifest=manifest(),
    )
    assert delivered == {
        "knowbot_id": "kb-1",
        "version": "1",
        "digest": "sha256:" + "b" * 64,
    }
    assert str(seen[0].url).endswith("/api/internal/v1/knowmart/deliveries")
    assert json.loads(seen[0].content)["owner_subject"] == "user-123"
    with pytest.raises(ServiceUnavailableError) as raised:
        await KnowmartDeliveryClient({}).deliver(
            delivery_id="x", owner_subject="u", knowbot_ref="r", manifest={}
        )
    assert raised.value.code == "knowmart_unconfigured"


@pytest.mark.asyncio
async def test_knowmart_public_request_and_stop() -> None:
    """交付可带申请公开、回上架状态；停用某一版走内部接口（SDD 0037）。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/stop") or "/unlisted/" in request.url.path:
            return httpx.Response(204)
        return httpx.Response(
            201,
            json={
                "knowbot_id": "kb-1",
                "version": "2",
                "digest": "sha256:" + "b" * 64,
                "listing_status": "pending",
            },
        )

    client = KnowmartDeliveryClient(
        KNOWMART, oidc_client=FakeOidc(), transport=httpx.MockTransport(handler)
    )
    delivered = await client.deliver(
        delivery_id="knowlab-expert-1",
        owner_subject="user-123",
        knowbot_ref="knowlab-r1",
        manifest=manifest("2"),
        request_public=True,
    )
    assert delivered["listing_status"] == "pending"
    assert json.loads(seen[0].content)["request_public"] is True
    await client.deliver(
        delivery_id="knowlab-expert-2",
        owner_subject="user-123",
        knowbot_ref="knowlab-r1",
        manifest=manifest("3"),
    )
    assert "request_public" not in json.loads(seen[1].content)  # 自用不带这一栏
    await client.stop(owner_subject="user-123", knowbot_id="kb-1", version="2")
    await client.set_unlisted(
        owner_subject="user-123",
        knowbot_ref="knowlab-r1",
        version="1",
        outcome="declined",
    )
    assert (seen[3].method, seen[3].url.path) == (
        "PUT",
        "/api/internal/v1/knowmart/owners/user-123/sources/knowlab-r1/unlisted/1",
    )
    assert json.loads(seen[3].content) == {"outcome": "declined"}
    assert seen[2].method == "POST"
    assert seen[2].url.path.endswith(
        "/api/internal/v1/knowmart/owners/user-123/knowbots/kb-1/versions/2/stop"
    )
