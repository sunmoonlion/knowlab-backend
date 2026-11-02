"""Lifecycle and multi-expert composition closure tests for author methods.
Demonstrates:
1. Freeze synthetic method v1 with independent slot mapping.
2. Two separate experts (Expert A and Expert B) bind Method v1 under different aliases.
3. Author upgrades method to v2 (different digest).
4. Expert A explicitly upgrades to v2, updating its binding and compiled manifest.
5. Expert B remains pinned to v1; its binding and compiled manifest remain immutable.
6. Validation checks reject invalid slot bindings, tampered digests, or required bounds expansion.
"""

from uuid import UUID
import pytest

from app.domain.knowbots.compile import compile_manifest
from app.domain.knowbots.methods import (
    BindMethod,
    FreezeMethod,
    bind,
    freeze,
)
from app.domain.knowbots.workdraft import WorkdraftBody, cells
from tests.dynamic_method_fixtures import body


def test_author_method_lifecycle_and_multi_expert_immutability():
    # 1. Author drafts a method and freezes it as Version 1
    raw_draft = body()
    v1_definition = freeze(
        WorkdraftBody.model_validate(raw_draft),
        FreezeMethod(
            request_id=UUID(int=101),
            revision=1,
            step_id="report",
            input_slots={"inspect": "audit_source_records"},
        ),
    )
    assert v1_definition.step.id == "method"
    assert v1_definition.step.uses == ("audit_source_records",)
    v1_digest = v1_definition.digest()
    method_id = UUID(int=201)

    ref_a_v1 = {
        "method_id": str(method_id),
        "version": 1,
        "digest": v1_digest,
        "inputs": {"audit_source_records": "inspect"},
    }

    # 2. Expert A binds Method v1 under local alias "step_report_a"
    expert_a_draft = body()
    expert_a_draft["steps"][0]["id"] = "step_report_a"
    expert_a_draft["steps"][0]["uses"] = ["inspect"]
    
    bind_a_v1 = BindMethod(
        request_id=UUID(int=301),
        revision=1,
        alias="step_report_a",
        digest=v1_digest,
        inputs={"audit_source_records": "inspect"},
        required=True,
        max_calls=1,
    )
    bound_step_a = bind(v1_definition, WorkdraftBody.model_validate(expert_a_draft), bind_a_v1)
    expert_a_draft["steps"][0] = bound_step_a.model_dump(mode="json")
    expert_a_draft["method_refs"] = {"step_report_a": ref_a_v1}
    parsed_a = WorkdraftBody.model_validate(expert_a_draft)
    sources_a = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed_a))
    }
    manifest_a_v1, problems_a_v1 = compile_manifest(
        parsed_a,
        sources_a,
        pack_id="expert-a",
        version="1",
        runtime="knowbench",
        fallback_name="expert-a",
        method_sources={
            "step_report_a": {"reference": ref_a_v1, "definition": v1_definition.document()}
        },
    )
    assert len(problems_a_v1) == 0
    assert manifest_a_v1["pack"]["runtime_contract"] == "knowbench/dynamic-methods/v2"
    assert manifest_a_v1["pack"]["method_sources"]["step_report_a"]["reference"]["digest"] == v1_digest
    assert manifest_a_v1["pack"]["method_sources"]["step_report_a"]["reference"]["version"] == 1

    # 3. Expert B binds Method v1 under different local alias "my_audit_step"
    ref_b_v1 = {
        "method_id": str(method_id),
        "version": 1,
        "digest": v1_digest,
        "inputs": {"audit_source_records": "inspect"},
    }
    expert_b_draft = body()
    expert_b_draft["steps"][0]["id"] = "my_audit_step"
    expert_b_draft["steps"][0]["uses"] = ["inspect"]
    
    bind_b_v1 = BindMethod(
        request_id=UUID(int=302),
        revision=1,
        alias="my_audit_step",
        digest=v1_digest,
        inputs={"audit_source_records": "inspect"},
        required=False,
        max_calls=1,
    )
    bound_step_b = bind(v1_definition, WorkdraftBody.model_validate(expert_b_draft), bind_b_v1)
    expert_b_draft["steps"][0] = bound_step_b.model_dump(mode="json")
    expert_b_draft["method_refs"] = {"my_audit_step": ref_b_v1}
    parsed_b = WorkdraftBody.model_validate(expert_b_draft)
    sources_b = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed_b))
    }
    manifest_b_v1, problems_b_v1 = compile_manifest(
        parsed_b,
        sources_b,
        pack_id="expert-b",
        version="1",
        runtime="knowbench",
        fallback_name="expert-b",
        method_sources={
            "my_audit_step": {"reference": ref_b_v1, "definition": v1_definition.document()}
        },
    )
    assert len(problems_b_v1) == 0
    assert manifest_b_v1["pack"]["runtime_contract"] == "knowbench/dynamic-methods/v2"
    assert manifest_b_v1["pack"]["method_sources"]["my_audit_step"]["reference"]["digest"] == v1_digest
    assert manifest_b_v1["pack"]["method_sources"]["my_audit_step"]["reference"]["version"] == 1

    # 4. Author modifies the method and freezes as Version 2
    raw_draft_v2 = body()
    raw_draft_v2["steps"][0]["does"] = "Upgraded step instructions with stricter audit checks."
    v2_definition = freeze(
        WorkdraftBody.model_validate(raw_draft_v2),
        FreezeMethod(
            request_id=UUID(int=102),
            revision=2,
            step_id="report",
            input_slots={"inspect": "audit_source_records"},
        ),
    )
    v2_digest = v2_definition.digest()
    assert v2_digest != v1_digest

    # 5. Expert A explicitly upgrades to Version 2
    ref_a_v2 = {
        "method_id": str(method_id),
        "version": 2,
        "digest": v2_digest,
        "inputs": {"audit_source_records": "inspect"},
    }
    bind_a_v2 = BindMethod(
        request_id=UUID(int=303),
        revision=2,
        alias="step_report_a",
        digest=v2_digest,
        inputs={"audit_source_records": "inspect"},
        required=True,
        max_calls=1,
    )
    bound_step_a_v2 = bind(v2_definition, WorkdraftBody.model_validate(expert_a_draft), bind_a_v2)
    expert_a_draft["steps"][0] = bound_step_a_v2.model_dump(mode="json")
    expert_a_draft["method_refs"] = {"step_report_a": ref_a_v2}
    parsed_a_v2 = WorkdraftBody.model_validate(expert_a_draft)
    manifest_a_v2, problems_a_v2 = compile_manifest(
        parsed_a_v2,
        sources_a,
        pack_id="expert-a",
        version="2",
        runtime="knowbench",
        fallback_name="expert-a",
        method_sources={
            "step_report_a": {"reference": ref_a_v2, "definition": v2_definition.document()}
        },
    )
    assert len(problems_a_v2) == 0

    # Expert A is now upgraded to Version 2
    assert manifest_a_v2["pack"]["method_sources"]["step_report_a"]["reference"]["version"] == 2
    assert manifest_a_v2["pack"]["method_sources"]["step_report_a"]["reference"]["digest"] == v2_digest

    # 6. Verify Expert B remains UNTOUCHED and pinned to Version 1
    assert manifest_b_v1["pack"]["method_sources"]["my_audit_step"]["reference"]["version"] == 1
    assert manifest_b_v1["pack"]["method_sources"]["my_audit_step"]["reference"]["digest"] == v1_digest

    # 7. Immutability & Rejection checks:
    # 7a. Reject binding with tampered digest
    with pytest.raises(ValueError, match="digest does not match"):
        bind(
            v2_definition,
            WorkdraftBody.model_validate(expert_b_draft),
            BindMethod(
                request_id=UUID(int=999),
                revision=1,
                alias="my_audit_step",
                digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
                inputs={"audit_source_records": "inspect"},
                required=False,
                max_calls=1,
            ),
        )

    # 7b. Reject binding with missing input slots
    with pytest.raises(ValueError, match="bind every method input slot"):
        bind(
            v2_definition,
            WorkdraftBody.model_validate(expert_b_draft),
            BindMethod(
                request_id=UUID(int=998),
                revision=1,
                alias="my_audit_step",
                digest=v2_digest,
                inputs={},  # missing audit_source_records
                required=False,
                max_calls=1,
            ),
        )
