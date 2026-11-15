"""Provider shape and coverage tests; sample receipts are not runtime evidence."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.application.errors.exceptions import BadGatewayError, ConcurrencyConflictError
from app.application.services.formal_methods import FormalMethodService
from app.domain.knowbots.formal_method_admission import (
    GOVERNANCE_CHECKS,
    FormalMethodValidationResult,
    FrozenFormalMethod,
)
from app.domain.knowbots.formal_method_execution import (
    FormalExecutionCase,
    FormalExecutionInput,
    FormalMethodExecutionRequest,
    FormalMethodExecutionResult,
    fingerprint,
)
from tests.test_formal_method_service import draft


def request_document(callers=("host",)):
    definition = draft().model_dump(mode="json", by_alias=True)
    definition["allowed_callers"] = list(callers)
    definition["definition"]["procedure"]["implementation"]["kind"] = "mcp"
    definition["source"].update(
        kind="mcp", asset_id=str(UUID(int=10, version=4)), asset_revision=1
    )
    cases = []
    for caller in callers:
        cases.append(
            {
                "case_id": caller,
                **({"caller": caller} if caller == "model" else {}),
                "input": {
                    "values": {"value": 3},
                    **(
                        {"model_prompt": "Call the candidate with the supplied values."}
                        if caller == "model"
                        else {}
                    ),
                },
                "repetitions": 1,
                "minimum_passes": 1,
                "expected": {
                    "outcome": "succeeded",
                    "rejection_code": None,
                    "assertions": [
                        {"kind": "equals", "pointer": "/result", "value": 3}
                    ],
                },
            }
        )
    suite = {"suite_id": str(UUID(int=20, version=4)), "version": 1, "cases": cases}
    return {
        "contract": "knowlab/formal-method-execution/v1",
        "run_id": str(UUID(int=1, version=4)),
        "owner_subject": "test-author",
        "request_id": str(UUID(int=2, version=4)),
        "workdraft_id": str(UUID(int=3, version=4)),
        "revision": 1,
        "method_id": str(UUID(int=4, version=4)),
        "draft": definition,
        "dependencies": [],
        "execution": {
            "suite": {**suite, "digest": fingerprint(suite)},
            "runner": {
                "kind": "user",
                "environment_id": str(UUID(int=30, version=4)),
                "workspace_root": "/workspace",
            },
            "budget": {"limit": "1", "currency": "CNY"},
            "timeout_seconds": 60,
            "max_wait_seconds": 60,
            **(
                {"model_probe": {"max_turns": 1, "max_method_calls": 1}}
                if "model" in callers
                else {}
            ),
        },
    }


def refreeze(document):
    suite = document["execution"]["suite"]
    suite["digest"] = fingerprint({k: v for k, v in suite.items() if k != "digest"})
    return document


def completed_document(request):
    admission = request.admission_request()
    receipt = {
        **admission.model_dump(mode="json", by_alias=True, exclude={"draft"}),
        "definition_digest": request.draft.definition.digest(),
        "source_digest": request.draft.source.fingerprint(),
        "checks": sorted(GOVERNANCE_CHECKS),
        "uncovered": [
            {
                "scenario": "Real service",
                "reason": "Contract fixture only",
                "scope": "Not executable evidence",
            }
        ],
        "completed_at": datetime(2026, 11, 15, tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "execution": {
            "contract": request.contract,
            "request_digest": request.fingerprint(),
            "suite_id": str(request.execution.suite.suite_id),
            "suite_version": 1,
            "suite_digest": request.execution.suite.digest,
            "evidence_digest": "sha256:" + "e" * 64,
            **(
                {"validated_callers": list(request.draft.allowed_callers)}
                if "model" in request.draft.allowed_callers
                else {}
            ),
        },
    }
    cases = []
    for index, case in enumerate(request.execution.suite.cases):
        cases.append(
            {
                "case_id": case.case_id,
                "repetition": 1,
                **(
                    {
                        "caller": "model",
                        "model_trace": {
                            "thread_id": "thread",
                            "turn_id": "turn",
                            "tool_call_id": "tool",
                            "method_call_id": str(UUID(int=100, version=4)),
                        },
                    }
                    if case.caller == "model"
                    else {}
                ),
                "status": "passed",
                "outcome": "succeeded",
                "rejection_code": None,
                "task_id": str(UUID(int=200 + index, version=4)),
                "attempt_id": str(UUID(int=300 + index, version=4)),
                "call_id": str(UUID(int=400 + index, version=4)),
                "assertions": [{"index": 0, "passed": True, "detail": "matched"}],
                "detail": "",
            }
        )
    return {
        "contract": request.contract,
        "run_id": str(request.run_id),
        "request_id": str(request.request_id),
        "method_id": str(request.method_id),
        "request_digest": request.fingerprint(),
        "suite": request.execution.suite.reference().model_dump(mode="json"),
        "status": "passed",
        "admission": {
            "contract": admission.contract,
            "run_id": str(request.run_id),
            "status": "passed",
            "receipt": receipt,
            "missing_checks": [],
            "details": [],
        },
        "cases": cases,
        "spent": "0",
        "currency": "CNY",
        "actions": [],
    }


def frozen_document(request, result):
    return {
        "contract": "knowlab/frozen-formal-method/v1",
        "owner_subject": request.owner_subject,
        "reference": {
            "method_id": str(request.method_id),
            "version": 1,
            "digest": request.draft.definition.digest(),
        },
        "draft": request.draft.model_dump(mode="json", by_alias=True),
        "validation": result["admission"]["receipt"],
        "visibility": "private",
    }


def test_host_request_result_and_suite_keep_the_original_canonical_shape():
    document = request_document()
    request = FormalMethodExecutionRequest.model_validate(document)
    assert request.model_dump(mode="json", by_alias=True) == document
    assert request.fingerprint() == fingerprint(document)
    explicit_defaults = deepcopy(document)
    case = explicit_defaults["execution"]["suite"]["cases"][0]
    case.update(caller="host")
    case["input"]["model_prompt"] = None
    explicit_defaults["execution"]["model_probe"] = None
    assert FormalMethodExecutionRequest.model_validate(explicit_defaults) == request
    result = completed_document(request)
    assert (
        FormalMethodExecutionResult.model_validate(result).model_dump(mode="json")
        == result
    )
    FrozenFormalMethod.model_validate(frozen_document(request, result))


@pytest.mark.parametrize("callers", [("model",), ("host", "model")])
def test_model_probe_keeps_grading_out_of_execution_input_and_binds_caller_coverage(
    callers,
):
    request = FormalMethodExecutionRequest.model_validate(request_document(callers))
    assert request.draft.definition.procedure.max_model_turns == 0
    case = request.execution.suite.cases[-1]
    assert case.execution_input() == {
        "values": {"value": 3},
        "model_prompt": case.input.model_prompt,
    }
    assert "expected" not in case.execution_input()
    result = completed_document(request)
    FormalMethodExecutionResult.model_validate(result)
    frozen = FrozenFormalMethod.model_validate(frozen_document(request, result))
    assert set(frozen.validation.execution.validated_callers) == set(callers)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_prompt",
        "host_prompt",
        "empty_prompt",
        "missing_limits",
        "too_many_turns",
        "boolean_limit",
        "too_many_calls",
        "host_limits",
        "missing_positive",
        "extra_caller",
        "invalid_rejection",
    ],
)
def test_probe_scope_and_positive_matrix_reject_incomplete_or_unbounded_requests(
    mutation,
):
    doc = request_document(("host", "model"))
    host, model = doc["execution"]["suite"]["cases"]
    if mutation == "missing_prompt":
        model["input"].pop("model_prompt")
    elif mutation == "host_prompt":
        host["input"]["model_prompt"] = "unexpected"
    elif mutation == "empty_prompt":
        model["input"]["model_prompt"] = "  "
    elif mutation == "missing_limits":
        doc["execution"].pop("model_probe")
    elif mutation == "too_many_turns":
        doc["execution"]["model_probe"]["max_turns"] = 2
    elif mutation == "boolean_limit":
        doc["execution"]["model_probe"]["max_turns"] = True
    elif mutation == "too_many_calls":
        doc["execution"]["model_probe"]["max_method_calls"] = 2
    elif mutation == "host_limits":
        doc["execution"]["suite"]["cases"] = [host]
        doc["draft"]["allowed_callers"] = ["host"]
    elif mutation == "missing_positive":
        model["expected"] = {
            "outcome": "rejected",
            "rejection_code": "approval_denied",
            "assertions": [],
        }
    elif mutation == "extra_caller":
        doc["draft"]["allowed_callers"] = ["host"]
    else:
        model["expected"] = {
            "outcome": "rejected",
            "rejection_code": "input_invalid",
            "assertions": [],
        }
    with pytest.raises(ValidationError):
        FormalMethodExecutionRequest.model_validate(refreeze(doc))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_coverage",
        "host_coverage",
        "duplicate_coverage",
        "no_trace",
        "no_call",
        "no_model_positive",
    ],
)
def test_passed_model_result_requires_coverage_and_exact_references(mutation):
    request = FormalMethodExecutionRequest.model_validate(
        request_document(("host", "model"))
    )
    result = completed_document(request)
    evidence = result["admission"]["receipt"]["execution"]
    case = result["cases"][-1]
    if mutation == "missing_coverage":
        evidence.pop("validated_callers")
    elif mutation == "host_coverage":
        evidence["validated_callers"] = ["host"]
    elif mutation == "duplicate_coverage":
        evidence["validated_callers"] = ["model", "model"]
    elif mutation == "no_trace":
        case.pop("model_trace")
    elif mutation == "no_call":
        case["call_id"] = None
    else:
        case.update(status="failed", outcome="failed")
    with pytest.raises(ValidationError):
        FormalMethodExecutionResult.model_validate(result)


@pytest.mark.parametrize("callers", [("model",), ("host", "model")])
@pytest.mark.parametrize("proof", ["none", "host"])
def test_old_host_or_static_receipt_never_freezes_model_callers(callers, proof):
    request = FormalMethodExecutionRequest.model_validate(request_document(callers))
    result = completed_document(request)
    receipt = result["admission"]["receipt"]
    if proof == "none":
        receipt.pop("execution")
    else:
        receipt["execution"].pop("validated_callers")
    with pytest.raises(ValidationError):
        FrozenFormalMethod.model_validate(frozen_document(request, result))
    with pytest.raises(ConcurrencyConflictError):
        FormalMethodService._receipt(
            request.admission_request(),
            FormalMethodValidationResult.model_validate(result["admission"]),
        )


def test_model_approval_rejection_and_pending_without_trace_are_valid_shapes():
    request = FormalMethodExecutionRequest.model_validate(request_document(("model",)))
    case = request.execution.suite.cases[0].model_dump(mode="json")
    case["expected"] = {
        "outcome": "rejected",
        "rejection_code": "approval_denied",
        "assertions": [],
    }
    FormalExecutionCase.model_validate(case)
    # The browser command still excludes the frozen digest and owns no evidence.
    command = request.execution.model_dump(mode="json")
    command["suite"].pop("digest")
    assert FormalExecutionInput.model_validate(command).freeze() == request.execution


@pytest.mark.parametrize("mutation", ["caller", "case", "repetition", "missing"])
def test_provider_checks_runtime_cases_against_its_own_frozen_suite(mutation):
    request = FormalMethodExecutionRequest.model_validate(request_document())
    document = completed_document(request)
    if mutation == "missing":
        suite = request.execution.suite.model_dump(mode="json")
        suite["cases"][0]["repetitions"] = 2
        wrapper = {"execution": {"suite": suite}}
        refreeze(wrapper)
        from app.domain.knowbots.formal_method_execution import FormalTestSuite

        frozen_suite = FormalTestSuite.model_validate(suite)
    else:
        frozen_suite = request.execution.suite
        document["status"] = "running"
        document["admission"].update(status="pending", receipt=None)
        document["cases"][0]["status"] = "running"
        if mutation == "caller":
            document["cases"][0]["caller"] = "model"
        elif mutation == "case":
            document["cases"][0]["case_id"] = "other"
        else:
            document["cases"][0]["repetition"] = 2
    report = FormalMethodExecutionResult.model_validate(document)
    with pytest.raises(BadGatewayError):
        FormalMethodService._execution_cases(report, frozen_suite)
