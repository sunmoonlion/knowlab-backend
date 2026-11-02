"""Test Assembly of Complete 20 Financial & Valuation Methods into Manifest.

Verifies:
- All 20 author methods freeze with valid provider schema and independent slots.
- Complete financial & valuation expert assembly compiles cleanly with 0 errors.
- Compiled manifest strictly adheres to knowbench/dynamic-methods/v2 contract.
- Read-only comparison against golden fixture (no overwrite in normal tests).
- Negative test: Plan skipping analysis steps is strictly rejected by knowbench.
- Positive test: Plan fulfilling full analysis DAG passes knowbench validate_plan.
"""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from app.domain.knowbots.methods import render
from tests.financial_author_method_fixtures import (
    ALL_20_SPECS,
    BATCH1_SPECS,
    BATCH2_SPECS,
    BATCH3_SPECS,
    build_financial_expert_manifest,
    make_author_method_definition,
)

PARENT = Path(__file__).resolve().parents[3]


def test_all_20_author_methods_validity() -> None:
    assert len(ALL_20_SPECS) == 20
    assert len(BATCH1_SPECS) == 8
    assert len(BATCH2_SPECS) == 6
    assert len(BATCH3_SPECS) == 6

    validator = jsonschema.Draft202012Validator(json.loads(render()))

    digests: set[str] = set()
    for spec in ALL_20_SPECS:
        defn = make_author_method_definition(spec)
        assert defn.step.id == "method"
        assert defn.step.selection is not None
        assert defn.step.selection.required is False
        assert len(defn.step.uses) > 0
        assert set(defn.step.uses) == set(defn.input_schemas.keys())

        # Validate against official provider schema
        validator.validate(defn.document())

        # Digest check
        digest = defn.digest()
        assert digest.startswith("sha256:")
        assert digest not in digests
        digests.add(digest)


def test_financial_expert_manifest_compilation_and_readonly_fixture() -> None:
    manifest, errors = build_financial_expert_manifest()
    assert not errors, f"Expected 0 compiler errors, got: {errors}"

    pack = manifest["pack"]
    assert pack["runtime_contract"] == "knowbench/dynamic-methods/v2"
    assert pack["method_compiler"] == "knowlab/workdraft-compiler/v1"

    # All 20 methods bound
    assert len(pack["method_sources"]) == 20
    for spec in ALL_20_SPECS:
        m_id = spec["id"]
        assert m_id in pack["method_sources"]
        source = pack["method_sources"][m_id]
        assert "reference" in source
        assert "definition" in source
        assert source["reference"]["version"] == 1
        assert source["reference"]["digest"].startswith("sha256:")

    # Validate pack against knowbench-dynamic-methods-v2 schema lock
    lock = json.loads(
        (PARENT / "contracts/knowbench-dynamic-methods-v2-lock.json").read_text()
    )
    schema_path = PARENT.parent / lock["provider"] / lock["schema"]
    jsonschema.validate(pack, json.loads(schema_path.read_text()))

    # Read-only verification against tracked golden fixture (Luna requirement)
    fixture_path = Path(__file__).parent / "fixtures/financial-expert-manifest.json"
    assert fixture_path.exists(), "Golden fixture must exist"
    golden = json.loads(fixture_path.read_text())
    assert manifest["pack"]["title"] == golden["pack"]["title"]
    assert len(manifest["pack"]["method_units"]) == len(golden["pack"]["method_units"])
    assert len(manifest["pack"]["method_sources"]) == len(
        golden["pack"]["method_sources"]
    )


def _load_knowbench_runtime() -> tuple[Any, Any, Any, Any, Any]:
    import app
    import app.domain

    kb_app = str(PARENT.parent / "knowbench-app/knowbench-backend/app/app")
    kb_dom = str(PARENT.parent / "knowbench-app/knowbench-backend/app/app/domain")
    if kb_app not in app.__path__:
        app.__path__.append(kb_app)
    if kb_dom not in app.domain.__path__:
        app.domain.__path__.append(kb_dom)

    import importlib

    catalog_mod = importlib.import_module("app.domain.workbench.method_catalog")
    plan_mod = importlib.import_module("app.domain.workbench.method_plan")

    return (
        catalog_mod.ReferencedMethodCatalog,
        plan_mod.validate_plan,
        plan_mod.MethodPlan,
        plan_mod.PlannedStep,
        plan_mod.InputBinding,
    )


def test_negative_two_step_plan_skipping_analysis_is_rejected() -> None:
    """Luna finding: A 2-step plan skipping analysis must NOT pass validation."""
    manifest, errors = build_financial_expert_manifest()
    assert not errors

    (
        ref_catalog_cls,
        validate_plan_fn,
        method_plan_cls,
        planned_step_cls,
        input_binding_cls,
    ) = _load_knowbench_runtime()

    catalog = ref_catalog_cls.model_validate(manifest["pack"])

    # Attempt 2-step plan: intake -> dossier directly
    two_step_proposal = method_plan_cls(
        contract="knowbench/method-plan/v1",
        parent_version=0,
        reason="attempt to skip analysis",
        steps=(
            planned_step_cls(
                instance_id="intake_1",
                method_id="financial_data_intake",
                inputs=(),
                tools=(),
                reason="intake",
            ),
            planned_step_cls(
                instance_id="report_1",
                method_id="comprehensive_dossier_formatting",
                inputs=(
                    input_binding_cls(
                        slot="financial_data_intake", instance_id="intake_1"
                    ),
                ),
                tools=(),
                reason="report",
            ),
        ),
        final_outputs={"comprehensive_dossier_formatting": "report_1"},
    )

    with pytest.raises(ValueError, match="method inputs do not match the author slots"):
        validate_plan_fn(
            catalog,
            two_step_proposal,
            previous=None,
            protected_prefix=0,
            completed_prefix=0,
            results={},
            available_tools=set(),
        )


def test_positive_complete_analysis_dag_plan_is_accepted() -> None:
    """Verify that a plan satisfying the full analysis DAG passes validation."""
    manifest, errors = build_financial_expert_manifest()
    assert not errors

    (
        ref_catalog_cls,
        validate_plan_fn,
        method_plan_cls,
        planned_step_cls,
        input_binding_cls,
    ) = _load_knowbench_runtime()

    catalog = ref_catalog_cls.model_validate(manifest["pack"])

    valid_steps = (
        planned_step_cls(
            instance_id="step_intake",
            method_id="financial_data_intake",
            inputs=(),
            tools=(),
            reason="intake",
        ),
        planned_step_cls(
            instance_id="step_m01",
            method_id="fin_statement_restructure",
            inputs=(
                input_binding_cls(
                    slot="financial_data_intake", instance_id="step_intake"
                ),
            ),
            tools=(),
            reason="m01",
        ),
        planned_step_cls(
            instance_id="step_m02",
            method_id="accounting_statement_reconciliation",
            inputs=(
                input_binding_cls(
                    slot="fin_statement_restructure", instance_id="step_m01"
                ),
            ),
            tools=(),
            reason="m02",
        ),
        planned_step_cls(
            instance_id="step_m03",
            method_id="non_recurring_items_purification",
            inputs=(
                input_binding_cls(
                    slot="accounting_statement_reconciliation",
                    instance_id="step_m02",
                ),
            ),
            tools=(),
            reason="m03",
        ),
        planned_step_cls(
            instance_id="step_m04",
            method_id="dupont_five_way_decomposition",
            inputs=(
                input_binding_cls(
                    slot="non_recurring_items_purification",
                    instance_id="step_m03",
                ),
            ),
            tools=(),
            reason="m04",
        ),
        planned_step_cls(
            instance_id="step_m05",
            method_id="solvency_liquidity_stress_check",
            inputs=(
                input_binding_cls(
                    slot="accounting_statement_reconciliation",
                    instance_id="step_m02",
                ),
            ),
            tools=(),
            reason="m05",
        ),
        planned_step_cls(
            instance_id="step_m06",
            method_id="working_capital_cash_cycle_analysis",
            inputs=(
                input_binding_cls(
                    slot="accounting_statement_reconciliation",
                    instance_id="step_m02",
                ),
            ),
            tools=(),
            reason="m06",
        ),
        planned_step_cls(
            instance_id="step_m07",
            method_id="earnings_quality_accrual_audit",
            inputs=(
                input_binding_cls(
                    slot="non_recurring_items_purification",
                    instance_id="step_m03",
                ),
            ),
            tools=(),
            reason="m07",
        ),
        planned_step_cls(
            instance_id="step_m13",
            method_id="three_statement_linked_forecasting",
            inputs=(
                input_binding_cls(
                    slot="dupont_five_way_decomposition", instance_id="step_m04"
                ),
                input_binding_cls(
                    slot="working_capital_cash_cycle_analysis",
                    instance_id="step_m06",
                ),
            ),
            tools=(),
            reason="m13",
        ),
        planned_step_cls(
            instance_id="step_m14",
            method_id="dcf_intrinsic_valuation",
            inputs=(
                input_binding_cls(
                    slot="three_statement_linked_forecasting",
                    instance_id="step_m13",
                ),
            ),
            tools=(),
            reason="m14",
        ),
        planned_step_cls(
            instance_id="step_m20",
            method_id="comprehensive_dossier_formatting",
            inputs=(
                input_binding_cls(
                    slot="accounting_statement_reconciliation",
                    instance_id="step_m02",
                ),
                input_binding_cls(
                    slot="dupont_five_way_decomposition", instance_id="step_m04"
                ),
                input_binding_cls(
                    slot="solvency_liquidity_stress_check",
                    instance_id="step_m05",
                ),
                input_binding_cls(
                    slot="earnings_quality_accrual_audit",
                    instance_id="step_m07",
                ),
                input_binding_cls(
                    slot="dcf_intrinsic_valuation", instance_id="step_m14"
                ),
            ),
            tools=(),
            reason="m20",
        ),
    )
    valid_proposal = method_plan_cls(
        contract="knowbench/method-plan/v1",
        parent_version=0,
        reason="valid full analysis plan",
        steps=valid_steps,
        final_outputs={"comprehensive_dossier_formatting": "step_m20"},
    )
    validate_plan_fn(
        catalog,
        valid_proposal,
        previous=None,
        protected_prefix=0,
        completed_prefix=0,
        results={},
        available_tools=set(),
    )
