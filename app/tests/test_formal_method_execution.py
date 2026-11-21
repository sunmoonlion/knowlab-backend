"""Contract and author-side tests; runtime doubles do not prove incubation."""

import asyncio
import copy
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.application.errors.exceptions import (
    BadGatewayError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.formal_method_admission import FreezeFormalMethod
from app.domain.knowbots.formal_method_execution import (
    ExecuteFormalMethod,
    FormalExecutionCase,
    FormalMethodExecutionRequest,
    FormalMethodExecutionResult,
    FormalTestSuiteDraft,
    evaluate_assertions,
)
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.test_formal_method_service import (
    OWNER,
    Repository,
    Runtime,
    draft,
    setup,
)
from tests.test_knowbot_repository_db import db as db


def suite():
    return {
        "suite_id": str(uuid4()),
        "version": 1,
        "cases": [
            {
                "case_id": "echo",
                "input": {"values": {"value": 3}},
                "repetitions": 1,
                "minimum_passes": 1,
                "expected": {
                    "outcome": "succeeded",
                    "assertions": [
                        {"kind": "equals", "pointer": "/result", "value": 3},
                    ],
                },
            }
        ],
    }


def execution():
    return {
        "suite": suite(),
        "runner": {
            "kind": "user",
            "environment_id": str(uuid4()),
            "workspace_root": "/allowed",
        },
        "budget": {"limit": "0", "currency": "CNY"},
        "timeout_seconds": 60,
        "max_wait_seconds": 60,
    }


class ExecutionRepository(Repository):
    def __init__(self):
        super().__init__()
        self.suites = {}

    async def formal_test_suite(self, owner, request_id, suite_id, version):
        value = self.suites.get((suite_id, version))
        return value[2] if value and value[:2] == (owner, request_id) else None

    async def freeze_formal_test_suite(self, owner, request_id, document):
        key = (UUID(document["suite_id"]), document["version"])
        record = (owner, request_id, document)
        if key in self.suites and self.suites[key] != record:
            raise ConcurrencyConflictError(
                "Immutable test suite", code="formal_suite_conflict"
            )
        self.suites[key] = record


class ExecutionRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.executions = {}
        self.submitted = {}

    async def submit_formal_method_execution(self, runtime, request):
        previous = self.submitted.get(request.run_id)
        if previous is not None:
            assert previous == request
        self.submitted[request.run_id] = request
        admission = await self.submit_formal_method_validation(
            runtime, request.admission_request()
        )
        return self.executions.setdefault(
            request.run_id,
            {
                "contract": request.contract,
                "run_id": str(request.run_id),
                "request_id": str(request.request_id),
                "method_id": str(request.method_id),
                "request_digest": request.fingerprint(),
                "suite": request.execution.suite.reference().model_dump(mode="json"),
                "status": "pending",
                "admission": admission,
                "cases": [],
                "spent": "0",
                "currency": "CNY",
                "actions": [],
            },
        )

    async def formal_method_execution(self, runtime, owner, run_id):
        return self.executions[UUID(run_id)]

    async def cancel_formal_method_execution(self, runtime, owner, run_id):
        result = self.executions[UUID(run_id)]
        result["status"] = "cancelled"
        result["admission"]["status"] = "cancelled"
        return result


async def prepared(repo=None, runtime=None, *, caller="host", carrier="mcp"):
    repo, runtime, service, mid, intake = await setup(
        repo or ExecutionRepository(), runtime or ExecutionRuntime()
    )
    body = draft().model_dump(mode="json", by_alias=True)
    body["allowed_callers"] = [caller]
    body["definition"]["procedure"]["implementation"]["kind"] = carrier
    if carrier == "skill_instruction":
        body["definition"]["procedure"]["max_model_turns"] = 1
    body["source"].update(kind=carrier, asset_id=str(uuid4()), asset_revision=1)
    await KnowbotWorkdraftService(repo).save(
        OWNER, str(intake.request_id), revision=1, edits=[("formal_method", body)]
    )
    spec = execution()
    if caller == "model":
        spec["suite"]["cases"][0]["caller"] = "model"
        spec["suite"]["cases"][0]["input"]["model_prompt"] = (
            "Call the candidate with the supplied values."
        )
        spec["model_probe"] = {"max_turns": 1, "max_method_calls": 1}
    command = ExecuteFormalMethod.model_validate(
        {
            "request_id": str(intake.request_id),
            "revision": 2,
            "run_id": str(uuid4()),
            "execution": spec,
        }
    )
    return repo, runtime, service, mid, command


@pytest.mark.asyncio
async def test_script_execution_request_is_host_only():
    _, runtime, service, mid, command = await prepared(carrier="skill_script")
    await service.execute(OWNER, str(mid), command)
    payload = runtime.submitted[command.run_id].model_dump(mode="json", by_alias=True)
    assert payload["draft"]["source"]["kind"] == "skill_script"
    _, runtime, service, mid, command = await prepared(caller="model")
    await service.execute(OWNER, str(mid), command)
    payload = runtime.submitted[command.run_id].model_dump(mode="json", by_alias=True)
    payload["draft"]["source"]["kind"] = "skill_script"
    payload["draft"]["definition"]["procedure"]["implementation"]["kind"] = (
        "skill_script"
    )
    with pytest.raises(ValidationError, match="host-only"):
        FormalMethodExecutionRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_instruction_execution_requires_exactly_one_host_inference():
    _, runtime, service, mid, command = await prepared(carrier="skill_instruction")
    await service.execute(OWNER, str(mid), command)
    payload = runtime.submitted[command.run_id].model_dump(mode="json", by_alias=True)
    for turns in (0, 2):
        payload["draft"]["definition"]["procedure"]["max_model_turns"] = turns
        with pytest.raises(ValidationError, match="single-turn|bounded model turns"):
            FormalMethodExecutionRequest.model_validate(payload)


def test_suite_digest_and_case_thresholds_are_independent_of_method():
    source = suite()
    frozen = FormalTestSuiteDraft.model_validate(source).freeze()
    assert frozen.digest == frozen.content_digest()
    changed = copy.deepcopy(source)
    changed["cases"][0]["input"]["values"]["value"] = 4
    assert FormalTestSuiteDraft.model_validate(changed).freeze().digest != frozen.digest
    for mutation in (
        {"minimum_passes": 2},
        {"repetitions": True},
        {
            "expected": {
                "outcome": "rejected",
                "rejection_code": "approval_denied",
                "assertions": [],
            }
        },
    ):
        changed = copy.deepcopy(source)
        changed["cases"][0].update(mutation)
        with pytest.raises(ValidationError):
            FormalTestSuiteDraft.model_validate(changed).freeze()


def test_assertions_use_exact_json_and_never_become_execution_input():
    case = FormalExecutionCase.model_validate(
        {
            **suite()["cases"][0],
            "expected": {
                "outcome": "succeeded",
                "assertions": [
                    {"kind": "equals", "pointer": "/number", "value": True},
                    {
                        "kind": "approx",
                        "pointer": "/boolean",
                        "value": 1,
                        "absolute_tolerance": 0,
                    },
                    {"kind": "contains", "pointer": "/a~1b/~0/0", "value": "safe"},
                    {"kind": "equals", "pointer": "/missing", "value": None},
                ],
            },
        }
    )
    result = evaluate_assertions(
        case, {"number": 1, "boolean": True, "a/b": {"~": ["safe"]}}
    )
    assert [r.passed for r in result] == [False, False, True, False]
    assert case.execution_input() == {"values": {"value": 3}}
    assert "expected" not in case.execution_input()
    with pytest.raises(ValidationError):
        FormalExecutionCase.model_validate(
            {**case.model_dump(), "input": {"values": {"bad": float("nan")}}}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["host", "model"])
async def test_execute_freezes_suite_before_runtime_and_recovers_without_model_key(
    caller,
):
    repo, runtime, service, mid, command = await prepared(caller=caller)
    pending = await service.execute(OWNER, str(mid), command)
    assert pending["status"] == "pending"
    payload = runtime.submitted[command.run_id]
    assert payload.runner == payload.execution.runner
    assert payload.admission_request().draft == payload.draft
    assert payload.execution.suite.cases[0].caller == caller
    assert "manifest" not in payload.model_dump()
    assert "runner" not in payload.model_dump()
    assert (
        FormalMethodExecutionRequest.model_validate_json(
            payload.model_dump_json(by_alias=True)
        )
        == payload
    )
    assert await service.execute(OWNER, str(mid), command) == pending
    full = await service.test_suite(
        OWNER, str(command.request_id), str(command.execution.suite.suite_id), 1
    )
    assert full["digest"] == payload.execution.suite.digest
    with pytest.raises(NotFoundError):
        await service.test_suite("other", str(command.request_id), full["suite_id"], 1)
    with pytest.raises(BadGatewayError):
        await service.cancel_execution(
            OWNER, str(uuid4()), str(command.request_id), str(command.run_id)
        )
    assert runtime.executions[command.run_id]["status"] == "pending"
    assert (
        await service.cancel_execution(
            OWNER, str(mid), str(command.request_id), str(command.run_id)
        )
    )["status"] == "cancelled"


@pytest.mark.asyncio
async def test_same_suite_version_cannot_silently_replace_expected_answers():
    _, runtime, service, mid, command = await prepared()
    await service.execute(OWNER, str(mid), command)
    changed = command.model_dump(mode="json")
    changed["run_id"] = str(uuid4())
    changed["execution"]["suite"]["cases"][0]["expected"]["assertions"][0]["value"] = 9
    with pytest.raises(ConcurrencyConflictError):
        await service.execute(
            OWNER, str(mid), ExecuteFormalMethod.model_validate(changed)
        )
    assert len(runtime.submitted) == 1


@pytest.mark.asyncio
async def test_completed_execution_reference_is_preserved_by_freeze_and_checked():
    # Test double checks only provider binding; real execution is tested in KB.
    _, runtime, service, mid, command = await prepared()
    await service.execute(OWNER, str(mid), command)
    payload = runtime.submitted[command.run_id]
    receipt = runtime.complete(command.run_id, business=False)
    receipt["execution"] = {
        "contract": payload.contract,
        "request_digest": payload.fingerprint(),
        "suite_id": str(payload.execution.suite.suite_id),
        "suite_version": 1,
        "suite_digest": payload.execution.suite.digest,
        "evidence_digest": "sha256:" + "e" * 64,
    }
    report = runtime.executions[command.run_id]
    report.update(
        status="passed",
        admission=runtime.validation_results[command.run_id],
        cases=[
            {
                "case_id": "echo",
                "repetition": 1,
                "status": "passed",
                "outcome": "succeeded",
                "rejection_code": None,
                "task_id": str(uuid4()),
                "attempt_id": str(uuid4()),
                "call_id": str(uuid4()),
                "assertions": [{"index": 0, "passed": True, "detail": "matched"}],
                "detail": "",
            }
        ],
    )
    FormalMethodExecutionResult.model_validate(report)
    for outcome in ("unknown", "cancelled"):
        corrupt = copy.deepcopy(report)
        corrupt["cases"][0]["outcome"] = outcome
        with pytest.raises(ValidationError):
            FormalMethodExecutionResult.model_validate(corrupt)
    frozen = await service.freeze(
        OWNER,
        str(mid),
        1,
        FreezeFormalMethod(
            request_id=command.request_id,
            revision=2,
            run_id=command.run_id,
            visibility="private",
        ),
    )
    assert frozen.provenance["validation"]["execution"] == receipt["execution"]
    assert frozen.provenance["validation"]["uncovered"]
    report["suite"]["digest"] = "sha256:" + "f" * 64
    with pytest.raises(BadGatewayError):
        await service.execution(
            OWNER, str(mid), str(command.request_id), str(command.run_id)
        )


@pytest.mark.asyncio
async def test_execution_outbound_paths_and_schema_aliases():
    import json

    import httpx

    from app.domain.knowbots.formal_method_execution import FormalExecutionDecision
    from app.infrastructure.external.knowbot_clients import RuntimeTestClients
    from tests.test_knowbot_clients import KNOWBENCH, FakeOidc

    _, runtime, service, mid, command = await prepared()
    await service.execute(OWNER, str(mid), command)
    payload = runtime.submitted[command.run_id]
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={})

    client = RuntimeTestClients(
        KNOWBENCH, oidc_client=FakeOidc(), transport=httpx.MockTransport(handle)
    )
    await client.submit_formal_method_execution("knowbench", payload)
    await client.formal_method_execution("knowbench", OWNER, str(command.run_id))
    await client.cancel_formal_method_execution("knowbench", OWNER, str(command.run_id))
    await client.decide_formal_method_execution(
        "knowbench",
        str(command.run_id),
        str(uuid4()),
        FormalExecutionDecision(
            owner_subject=OWNER,
            case_id="echo",
            repetition=1,
            token="test-token",
            subject_digest="d" * 64,
            decision="decline",
        ),
    )
    posted = json.loads(seen[0].content)
    assert "schema" in posted["draft"]["definition"]["procedure"]["inputs"]["value"]
    assert "schema_" not in seen[0].content.decode()
    assert FormalMethodExecutionRequest.model_validate(posted) == payload
    assert [r.method for r in seen] == ["POST", "GET", "POST", "POST"]
    assert seen[2].url.path.endswith("/cancel")
    assert seen[2].url.params["owner_subject"] == OWNER
    assert "/actions/" in str(seen[3].url)
    assert json.loads(seen[3].content)["owner_subject"] == OWNER


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["host", "model"])
async def test_postgres_suite_concurrent_freeze_and_new_version(db, caller):
    runtime = ExecutionRuntime()
    async with db() as session:
        _, _, _, mid, command = await prepared(
            SqlKnowlabRepository(session), runtime, caller=caller
        )

    async def execute_once(value):
        from app.application.services.formal_methods import FormalMethodService

        async with db() as session:
            return await FormalMethodService(
                SqlKnowlabRepository(session), runtime
            ).execute(OWNER, str(mid), value)

    first, retry = await asyncio.gather(execute_once(command), execute_once(command))
    assert first == retry
    changed = command.model_dump(mode="json")
    changed["run_id"] = str(uuid4())
    changed["execution"]["suite"]["cases"][0]["input"]["values"]["value"] = 5
    with pytest.raises(ConcurrencyConflictError):
        await execute_once(ExecuteFormalMethod.model_validate(changed))
    changed["execution"]["suite"]["version"] = 2
    newer = await execute_once(ExecuteFormalMethod.model_validate(changed))
    assert newer["suite"]["digest"] != first["suite"]["digest"]
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        assert (
            await repo.formal_test_suite(
                "other", command.request_id, command.execution.suite.suite_id, 1
            )
            is None
        )
        old = await repo.formal_test_suite(
            OWNER, command.request_id, command.execution.suite.suite_id, 1
        )
        assert old["cases"][0]["input"]["values"]["value"] == 3
        assert old["cases"][0].get("caller", "host") == caller
        assert ("model_prompt" in old["cases"][0]["input"]) == (caller == "model")
