"""Optional dynamic outputs keep the existing test contract and predicates."""

import copy

import pytest
from pydantic import ValidationError

from app.application.errors.exceptions import BadRequestError
from app.application.services.knowbot_service import _test_request
from app.domain.knowbots.test_run import CaseOutputsV1, RunRequestV1
from tests.dynamic_method_fixtures import build
from tests.test_knowbot_test_outputs import outputs


def request():
    manifest, errors = build()
    assert not errors
    return {
        "contract": "knowbot-test-run/v1",
        "run_id": "synthetic-method-test",
        "owner_subject": "author",
        "runner": {
            "kind": "user",
            "environment_id": "00000000-0000-0000-0000-000000000001",
            "workspace_root": "/work",
        },
        "manifest": manifest,
        "cases": [
            {
                "case_id": "c1",
                "input": {"question": "synthetic question"},
                "expectations": [
                    {
                        "artifact": "report",
                        "path": "rows.0.id",
                        "kind": "equals",
                        "value": "one",
                    }
                ],
            }
        ],
        "budget": {"limit": "1"},
        "timeout_seconds": 600,
    }


def final():
    return {
        "method_id": "report",
        "instance_id": "report_42",
        "result": {
            "id": "00000000-0000-0000-0000-000000000002",
            "version": 2,
            "digest": "a" * 64,
        },
        "artifact": {"rows": [{"id": "one"}]},
        "deliverable": {
            "path": "report.md",
            "text": "synthetic",
            "step_id": "report_42",
        },
    }


def test_dynamic_expectations_name_the_authors_final_not_a_runtime_instance():
    given = request()
    assert (
        RunRequestV1.model_validate(given).cases[0].expectations[0].artifact == "report"
    )
    for name in ("inspect", "report_42", "__method_output__:report_42", "unknown"):
        changed = copy.deepcopy(given)
        changed["cases"][0]["expectations"][0]["artifact"] = name
        with pytest.raises(ValidationError, match="declared final"):
            RunRequestV1.model_validate(changed)
        with pytest.raises(BadRequestError) as error:
            _test_request(changed)
        assert error.value.code == "test_request_invalid"


def test_old_outputs_serialize_without_optional_dynamic_fields():
    legacy = outputs("synthetic-method-test").model_dump(mode="json")
    assert "plan_version" not in legacy and "final_outputs" not in legacy
    assert all("current" not in step for step in legacy["steps"])


def test_dynamic_output_references_files_and_content_roundtrip():
    body = outputs("synthetic-method-test").model_dump(mode="json")
    body.update(deliverable=None, plan_version=2, final_outputs=[final()])
    body["steps"][0]["current"] = False
    parsed = CaseOutputsV1.model_validate(body)
    assert parsed.final_outputs[0].result.version == 2
    assert parsed.final_outputs[0].deliverable.text == "synthetic"
    assert parsed.steps[0].current is False
    assert CaseOutputsV1.model_validate(parsed.model_dump(mode="json")) == parsed


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.update(plan_version=None),
        lambda b: b["final_outputs"].append(copy.deepcopy(b["final_outputs"][0])),
        lambda b: b["final_outputs"][0].update(result=None),
        lambda b: b["final_outputs"][0]["deliverable"].update(step_id="other"),
        lambda b: b["final_outputs"][0]["result"].update(digest="invalid"),
        lambda b: b["final_outputs"][0].update(artifact="x" * 200_001),
    ],
)
def test_ambiguous_or_unverified_final_outputs_are_rejected(change):
    body = {
        "contract": "knowbot-test-run/v1",
        "run_id": "synthetic-method-test",
        "case_id": "c1",
        "plan_version": 2,
        "final_outputs": [final()],
    }
    change(body)
    with pytest.raises(ValidationError):
        CaseOutputsV1.model_validate(body)
