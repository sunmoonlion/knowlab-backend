"""knowlab 的两个外部调用（SDD 0027）：调运行方跑测试、交付 knowmart。

运行方是「登记」出来的：本应用 service_clients 里每个 relation=testrun 的目标就是一个运
行方，部署生成对应的环境变量（见 service_client.py）。现在只有 investment；加运行方只加
配置。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.application.errors.exceptions import (
    BadGatewayError,
    BadRequestError,
    ServiceUnavailableError,
)
from app.domain.knowbots.test_run import RunRequestV1, RunResultV1
from app.infrastructure.external.service_client import ServiceClient, service_relation

RUNTIME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TEST_RUNS = "/test-runs"  # 接在运行方入口 /api/internal/v1/knowbot-runtime 后面


def registered_runtimes(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """环境里配全了 testrun 关系的目标。"""
    env = os.environ if environ is None else environ
    found = set()
    for key in env:
        if key.endswith("_TESTRUN_SERVICE_CLIENT_ID"):
            name = key[: -len("_TESTRUN_SERVICE_CLIENT_ID")].lower().replace("_", "-")
            if RUNTIME.fullmatch(name) and service_relation(name, "testrun", env):
                found.add(name)
    return frozenset(found)


def _result(body: Any) -> RunResultV1:
    try:
        return RunResultV1.model_validate(body)
    except ValueError as exc:
        raise BadGatewayError(
            "The runtime answered outside knowbot-test-run/v1",
            code="test_run_contract_invalid",
        ) from exc


class RuntimeTestClients:
    def __init__(self, environ: Mapping[str, str] | None = None, **client_options: Any):
        self._environ = environ
        self._options = client_options
        self._clients: dict[str, ServiceClient] = {}

    def registered(self) -> frozenset[str]:
        return registered_runtimes(self._environ)

    def _client(self, runtime: str) -> ServiceClient:
        if runtime not in self._clients:
            relation = service_relation(runtime, "testrun", self._environ)
            if relation is None:
                raise BadRequestError(
                    "This runtime is not registered", code="runtime_not_registered"
                )
            self._clients[runtime] = ServiceClient(relation, **self._options)
        return self._clients[runtime]

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1:
        status, body = await self._client(runtime).request(
            "POST", TEST_RUNS, json=request.model_dump(mode="json")
        )
        result = _result(body)
        if result.run_id != request.run_id:
            raise BadGatewayError(
                "The runtime answered another run", code="run_mismatch"
            )
        return result

    async def status(self, runtime: str, run_id: str) -> RunResultV1:
        _, body = await self._client(runtime).request(
            "GET", f"{TEST_RUNS}/{quote(run_id, safe='')}"
        )
        return _result(body)


class KnowmartDeliveryClient:
    def __init__(self, environ: Mapping[str, str] | None = None, **client_options: Any):
        self._environ = environ
        self._options = client_options
        self._client: ServiceClient | None = None

    async def deliver(
        self,
        *,
        delivery_id: str,
        owner_subject: str,
        knowbot_ref: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        if self._client is None:
            relation = service_relation("knowmart", "deliver", self._environ)
            if relation is None:
                raise ServiceUnavailableError(
                    "Delivery to knowmart is not configured",
                    code="knowmart_unconfigured",
                )
            self._client = ServiceClient(relation, **self._options)
        _, body = await self._client.request(
            "POST",
            "/deliveries",
            json={
                "delivery_id": delivery_id,
                "owner_subject": owner_subject,
                "knowbot_ref": knowbot_ref,
                "manifest": manifest,
            },
        )
        if not isinstance(body, dict) or not body.get("knowbot_id"):
            raise BadGatewayError(
                "knowmart answered without a knowbot", code="knowmart_contract_invalid"
            )
        return {
            "knowbot_id": body["knowbot_id"],
            "version": body.get("version"),
            "digest": body.get("digest"),
        }
