"""knowlab 的外部调用（SDD 0027）：调运行方跑测试、交运行方跑孵化的一轮、交付与停用
knowmart 里的专家。

运行方是「登记」出来的：本应用 service_clients 里每个 relation=testrun 的目标就是一个运
行方，部署生成对应的环境变量（见 service_client.py）。现在只有 knowbench；加运行方只加
配置。孵化（专家孵化方案第 8.2 节）只交 knowbench，关系 incubate（scope
`knowbench:incubate`，环境变量 `KNOWBENCH_INCUBATE_SERVICE_*`，入口同测试的
`KNOWBENCH_INTERNAL_URL`）。
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
from app.domain.knowbots.incubation import IncubationRequestV1, IncubationResultV1
from app.domain.knowbots.test_run import (
    AnswersV1,
    CaseOutputsV1,
    RunRequestV1,
    RunResultV1,
)
from app.infrastructure.external.service_client import ServiceClient, service_relation

RUNTIME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TEST_RUNS = "/test-runs"  # 接在运行方入口 /api/internal/v1/knowbot-runtime 后面
USERS = "/users"  # 同上：用户接入过的电脑
INCUBATIONS = "/incubations"  # 同上：孵化的一轮
INCUBATOR = "knowbench"


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

    async def answer(
        self, runtime: str, run_id: str, answers: AnswersV1
    ) -> RunResultV1:
        _, body = await self._client(runtime).request(
            "POST",
            f"{TEST_RUNS}/{quote(run_id, safe='')}/answers",
            json=answers.model_dump(mode="json", exclude_none=True),
        )
        result = _result(body)
        if result.run_id != run_id:
            raise BadGatewayError(
                "The runtime answered another run", code="run_mismatch"
            )
        return result

    async def computers(self, runtime: str, subject: str) -> list[dict[str, Any]]:
        """用户接入过的电脑（SDD 0046 第三节「怎么选电脑」）。"""
        _, body = await self._client(runtime).request(
            "GET", f"{USERS}/{quote(subject, safe='')}/computers"
        )
        found = body.get("computers") if isinstance(body, dict) else None
        return [c for c in found or [] if isinstance(c, dict)]

    async def outputs(
        self, runtime: str, run_id: str, case_id: str
    ) -> CaseOutputsV1 | None:
        """一条用例跑完后的成稿与各步交回物（SDD 0037）。

        运行方说这条用例没有（没取、已清空）回 None。运行方还是旧版、没有这个接口
        （404 而不是运行方自己的 not_found）当暂时取不到：升级混跑时一次碰到旧实例
        不该永远记成没有。
        """
        run, case = quote(run_id, safe=""), quote(case_id, safe="")
        status, body = await self._client(runtime).request(
            "GET", f"{TEST_RUNS}/{run}/cases/{case}/outputs", accept=(404,)
        )
        if status == 404:
            if isinstance(body, dict) and body.get("code") == "not_found":
                return None
            raise ServiceUnavailableError(
                "The runtime cannot give outputs yet",
                code="runtime_outputs_unsupported",
            )
        return _outputs(body, run_id, case_id)


def _outputs(body: Any, run_id: str, case_id: str) -> CaseOutputsV1:
    try:
        found = CaseOutputsV1.model_validate(body)
    except ValueError as exc:
        raise BadGatewayError(
            "The runtime answered outside knowbot-test-run/v1",
            code="test_run_contract_invalid",
        ) from exc
    if (found.run_id, found.case_id) != (run_id, case_id):
        raise BadGatewayError("The runtime answered another case", code="run_mismatch")
    return found


def _incubation(body: Any, incubation_id: str) -> IncubationResultV1:
    try:
        found = IncubationResultV1.model_validate(body)
    except ValueError as exc:
        raise BadGatewayError(
            "The runtime answered outside knowbot-incubation/v1",
            code="incubation_contract_invalid",
        ) from exc
    if found.incubation_id != incubation_id:
        raise BadGatewayError(
            "The runtime answered another round", code="incubation_mismatch"
        )
    return found


class KnowbenchIncubationClient:
    """交 knowbench 跑孵化的一轮（服务关系 knowbench:incubate，契约
    knowbot-incubation/v1）：提交（幂等）、查、取消。"""

    def __init__(self, environ: Mapping[str, str] | None = None, **client_options: Any):
        self._environ = environ
        self._options = client_options
        self._client: ServiceClient | None = None

    def configured(self) -> bool:
        return service_relation(INCUBATOR, "incubate", self._environ) is not None

    def _service(self) -> ServiceClient:
        if self._client is None:
            relation = service_relation(INCUBATOR, "incubate", self._environ)
            if relation is None:
                raise ServiceUnavailableError(
                    "Incubation on knowbench is not configured",
                    code="incubation_unconfigured",
                )
            self._client = ServiceClient(relation, **self._options)
        return self._client

    async def start(self, request: IncubationRequestV1) -> IncubationResultV1:
        _, body = await self._service().request(
            "POST", INCUBATIONS, json=request.model_dump(mode="json")
        )
        return _incubation(body, request.incubation_id)

    async def status(self, incubation_id: str) -> IncubationResultV1:
        _, body = await self._service().request(
            "GET", f"{INCUBATIONS}/{quote(incubation_id, safe='')}"
        )
        return _incubation(body, incubation_id)

    async def cancel(self, incubation_id: str) -> IncubationResultV1:
        _, body = await self._service().request(
            "POST", f"{INCUBATIONS}/{quote(incubation_id, safe='')}/cancel"
        )
        return _incubation(body, incubation_id)


class KnowmartDeliveryClient:
    """交 knowmart、停用 knowmart 里的某一版（服务关系 knowmart:deliver）。"""

    def __init__(self, environ: Mapping[str, str] | None = None, **client_options: Any):
        self._environ = environ
        self._options = client_options
        self._client: ServiceClient | None = None

    def _service(self) -> ServiceClient:
        if self._client is None:
            relation = service_relation("knowmart", "deliver", self._environ)
            if relation is None:
                raise ServiceUnavailableError(
                    "Delivery to knowmart is not configured",
                    code="knowmart_unconfigured",
                )
            self._client = ServiceClient(relation, **self._options)
        return self._client

    async def deliver(
        self,
        *,
        delivery_id: str,
        owner_subject: str,
        knowbot_ref: str,
        manifest: dict[str, Any],
        request_public: bool = False,
    ) -> dict[str, Any]:
        body_out: dict[str, Any] = {
            "delivery_id": delivery_id,
            "owner_subject": owner_subject,
            "knowbot_ref": knowbot_ref,
            "manifest": manifest,
        }
        if request_public:
            body_out["request_public"] = True
        _, body = await self._service().request("POST", "/deliveries", json=body_out)
        if not isinstance(body, dict) or not body.get("knowbot_id"):
            raise BadGatewayError(
                "knowmart answered without a knowbot", code="knowmart_contract_invalid"
            )
        found = {
            "knowbot_id": body["knowbot_id"],
            "version": body.get("version"),
            "digest": body.get("digest"),
        }
        if isinstance(body.get("listing_status"), str):
            found["listing_status"] = body["listing_status"]
        return found

    async def stop(self, *, owner_subject: str, knowbot_id: str, version: str) -> None:
        owner, knowbot = quote(owner_subject, safe=""), quote(knowbot_id, safe="")
        number = quote(version, safe="")
        await self._service().request(
            "POST", f"/owners/{owner}/knowbots/{knowbot}/versions/{number}/stop"
        )

    async def set_unlisted(
        self, *, owner_subject: str, knowbot_ref: str, version: str, outcome: str
    ) -> None:
        owner, ref = quote(owner_subject, safe=""), quote(knowbot_ref, safe="")
        number = quote(version, safe="")
        await self._service().request(
            "PUT",
            f"/owners/{owner}/sources/{ref}/unlisted/{number}",
            json={"outcome": outcome},
        )
