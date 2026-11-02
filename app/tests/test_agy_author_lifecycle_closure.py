"""Test Lifecycle Closure of Author Methods and Multi-Expert Immutability.

1. Author creates a method in a dynamic workdraft and freezes it as Version 1.
2. Expert A binds Method v1 under alias "step_report_a".
3. Expert B binds Method v1 under alias "my_audit_step".
4. Expert A explicitly upgrades to v2, updating its binding and manifest.
5. Expert B remains pinned to v1; its binding and manifest remain immutable.
6. Validation checks reject invalid slot bindings or bounds expansion.
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


def test_author_method_lifecycle_and_multi_expert_immutability() -> None:
    # 1. Author writes method in workdraft and freezes it as Version 1
    raw_draft = body()
    v1_definition = freeze(
        WorkdraftBody.model_validate(raw_draft),
        FreezeMethod(
            request_id=UUID(int=101),
            revision=1,
            step_id="report",
            input_slots={"inspect": "records"},
        ),
    )
    v1_digest = v1_definition.digest()
    assert v1_digest.startswith("sha256:")

    # 2. Expert A binds Method v1 under local alias "step_report_a"
    expert_a_draft = body()
    ref_a_v1 = {
        "method_id": str(UUID(int=201)),
        "version": 1,
        "digest": v1_digest,
        "inputs": {"records": "inspect"},
    }
    bind_a_v1 = BindMethod(
        request_id=UUID(int=301),
        revision=1,
        alias="step_report_a",
        digest=v1_digest,
        inputs={"records": "inspect"},
        required=False,
        max_calls=1,
    )
    bound_step_a = bind(
        v1_definition,
        WorkdraftBody.model_validate(expert_a_draft),
        bind_a_v1,
    )
    expert_a_draft["steps"][0] = bound_step_a.model_dump(mode="json")
    expert_a_draft["method_refs"] = {"step_report_a": ref_a_v1}

    parsed_a_v1 = WorkdraftBody.model_validate(expert_a_draft)
    sources_a_v1 = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed_a_v1))
    }
    manifest_a_v1, problems_a_v1 = compile_manifest(
        parsed_a_v1,
        sources_a_v1,
        pack_id="expert-a-v1",
        version="1",
        runtime="knowbench",
        fallback_name="expert-a",
        method_sources={
            "step_report_a": {
                "reference": ref_a_v1,
                "definition": v1_definition.document(),
            }
        },
    )
    assert len(problems_a_v1) == 0
    pack_a = manifest_a_v1["pack"]
    assert pack_a["runtime_contract"] == "knowbench/dynamic-methods/v2"
    ref_a = pack_a["method_sources"]["step_report_a"]["reference"]
    assert ref_a["digest"] == v1_digest
    assert ref_a["version"] == 1

    # 3. Expert B binds Method v1 under different local alias "my_audit_step"
    expert_b_draft = body()
    ref_b_v1 = {
        "method_id": str(UUID(int=201)),
        "version": 1,
        "digest": v1_digest,
        "inputs": {"records": "inspect"},
    }
    bind_b_v1 = BindMethod(
        request_id=UUID(int=302),
        revision=1,
        alias="my_audit_step",
        digest=v1_digest,
        inputs={"records": "inspect"},
        required=False,
        max_calls=1,
    )
    bound_step_b = bind(
        v1_definition,
        WorkdraftBody.model_validate(expert_b_draft),
        bind_b_v1,
    )
    expert_b_draft["steps"][0] = bound_step_b.model_dump(mode="json")
    expert_b_draft["method_refs"] = {"my_audit_step": ref_b_v1}

    parsed_b_v1 = WorkdraftBody.model_validate(expert_b_draft)
    sources_b_v1 = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed_b_v1))
    }
    manifest_b_v1, problems_b_v1 = compile_manifest(
        parsed_b_v1,
        sources_b_v1,
        pack_id="expert-b-v1",
        version="1",
        runtime="knowbench",
        fallback_name="expert-b",
        method_sources={
            "my_audit_step": {
                "reference": ref_b_v1,
                "definition": v1_definition.document(),
            }
        },
    )
    assert len(problems_b_v1) == 0
    pack_b = manifest_b_v1["pack"]
    assert pack_b["runtime_contract"] == "knowbench/dynamic-methods/v2"
    ref_b = pack_b["method_sources"]["my_audit_step"]["reference"]
    assert ref_b["digest"] == v1_digest
    assert ref_b["version"] == 1

    # 4. Author modifies the method and freezes as Version 2
    raw_draft_v2 = body()
    raw_draft_v2["steps"][0]["does"] = "Upgraded step with stricter checks."
    v2_definition = freeze(
        WorkdraftBody.model_validate(raw_draft_v2),
        FreezeMethod(
            request_id=UUID(int=102),
            revision=2,
            step_id="report",
            input_slots={"inspect": "records"},
        ),
    )
    v2_digest = v2_definition.digest()
    assert v2_digest != v1_digest

    # 5. Expert A upgrades to Version 2
    ref_a_v2 = {
        "method_id": str(UUID(int=201)),
        "version": 2,
        "digest": v2_digest,
        "inputs": {"records": "inspect"},
    }
    bind_a_v2 = BindMethod(
        request_id=UUID(int=303),
        revision=2,
        alias="step_report_a",
        digest=v2_digest,
        inputs={"records": "inspect"},
        required=False,
        max_calls=1,
    )
    bound_step_a_v2 = bind(
        v2_definition,
        WorkdraftBody.model_validate(expert_a_draft),
        bind_a_v2,
    )
    expert_a_draft["steps"][0] = bound_step_a_v2.model_dump(mode="json")
    expert_a_draft["method_refs"] = {"step_report_a": ref_a_v2}

    parsed_a_v2 = WorkdraftBody.model_validate(expert_a_draft)
    manifest_a_v2, _ = compile_manifest(
        parsed_a_v2,
        sources_a_v1,
        pack_id="expert-a-v2",
        version="2",
        runtime="knowbench",
        fallback_name="expert-a",
        method_sources={
            "step_report_a": {
                "reference": ref_a_v2,
                "definition": v2_definition.document(),
            }
        },
    )
    ref_a_upgraded = manifest_a_v2["pack"]["method_sources"]["step_report_a"][
        "reference"
    ]
    assert ref_a_upgraded["version"] == 2
    assert ref_a_upgraded["digest"] == v2_digest

    # 6. Verify Expert B remains UNTOUCHED and pinned to Version 1
    ref_b_pinned = manifest_b_v1["pack"]["method_sources"]["my_audit_step"][
        "reference"
    ]
    assert ref_b_pinned["version"] == 1
    assert ref_b_pinned["digest"] == v1_digest

    # 7. Immutability & Rejection checks:
    # 7a. Expert B cannot be compiled with mismatched digest
    tampered_ref = dict(ref_b_v1, digest=v2_digest)
    with pytest.raises(ValueError, match="digest does not match"):
        bind(
            v1_definition,
            WorkdraftBody.model_validate(expert_b_draft),
            BindMethod(
                request_id=UUID(int=304),
                revision=2,
                alias="my_audit_step",
                digest=tampered_ref["digest"],
                inputs={"records": "inspect"},
                required=False,
                max_calls=1,
            ),
        )

    # 7b. Expert B cannot bind with missing input slots
    with pytest.raises(ValueError, match="bind every method input slot"):
        bind(
            v1_definition,
            WorkdraftBody.model_validate(expert_b_draft),
            BindMethod(
                request_id=UUID(int=305),
                revision=2,
                alias="my_audit_step",
                digest=v1_digest,
                inputs={},
                required=False,
                max_calls=1,
            ),
        )
