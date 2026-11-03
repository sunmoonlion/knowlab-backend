import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.application.services.knowbot_service import _test_request
from app.domain.knowbots.test_run import RunRequestV1, RunRequestV2


def payload():
    return {
        "contract": "knowbot-test-run/v2",
        "run_id": "dynamic-test-v2",
        "owner_subject": "author",
        "budget": {"limit": "1"},
        "timeout_seconds": 600,
        "runner": {
            "kind": "user",
            "environment_id": "00000000-0000-0000-0000-000000000001",
            "workspace_root": "/work",
        },
        "manifest": {
            "schema": "knowbot-manifest/v3",
            "pack": {
                "runtime_contract": "knowbench/dynamic-methods/v3",
                "planning": {"final_units": ["report"]},
                "method_units": [
                    {
                        "contract": {"step_id": "analysis"},
                        "selection": {"max_calls": 1},
                    },
                    {
                        "contract": {"step_id": "repeated"},
                        "selection": {"max_calls": 2},
                    },
                ],
            },
        },
        "cases": [
            {
                "case_id": "c1",
                "input": {"question": "synthetic"},
                "expectations": [
                    {
                        "artifact": "analysis",
                        "path": "amount",
                        "kind": "approx",
                        "value": 100,
                        "tolerance": 0.001,
                    }
                ],
            }
        ],
    }


def test_v2_preserves_intermediate_assertions_and_v1_remains_strict():
    raw = payload()
    parsed = RunRequestV2.model_validate(raw)
    assert (
        parsed.cases[0].expectations[0].model_dump(mode="json")
        == raw["cases"][0]["expectations"][0]
    )
    assert isinstance(_test_request(raw), RunRequestV2)
    with pytest.raises(ValidationError, match="declared final"):
        RunRequestV1.model_validate({**raw, "contract": "knowbot-test-run/v1"})
    for name in ("repeated", "analysis_1", "missing"):
        raw["cases"][0]["expectations"][0]["artifact"] = name
        with pytest.raises(ValidationError):
            RunRequestV2.model_validate(raw)


def test_v2_is_explicit_and_schema_is_generated():
    raw = payload()
    raw["manifest"]["pack"]["runtime_contract"] = "knowbench/dynamic-methods/v2"
    with pytest.raises(ValidationError, match="v3 capability"):
        RunRequestV2.model_validate(raw)
    parent = Path(__file__).resolve().parents[3]
    file = parent / "contracts/knowbot-test-run/v2/test-run-request.schema.json"
    schema = json.loads(file.read_text())
    assert {
        k: v for k, v in schema.items() if not k.startswith("$") or k == "$defs"
    } == RunRequestV2.model_json_schema()
    lock = json.loads(
        (
            parent.parent / "knowbench-app/contracts/knowlab-test-run-v2-lock.json"
        ).read_text()
    )
    assert hashlib.sha256(file.read_bytes()).hexdigest() == lock["sha256"]
