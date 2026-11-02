"""Test Assembly of Complete 20 Financial & Valuation Methods into Manifest.

Verifies:
- All 20 author methods freeze with valid provider schema and independent slots.
- Complete financial & valuation expert assembly compiles cleanly with 0 errors.
- Compiled manifest strictly adheres to knowbench/dynamic-methods/v2 contract.
- Read-only comparison against golden fixture (assert manifest == golden).
- Golden fixture tampering is detected and fails verification.
- Isolated process execution: 2-step plan skipping analysis steps is rejected.
- Isolated process execution: Plan fulfilling full analysis DAG passes validation.
"""

import json
import os
import subprocess
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
KNOWBENCH_APP_DIR = (PARENT.parent / "knowbench-app/knowbench-backend/app").resolve()
GOLDEN_FIXTURE_PATH = Path(__file__).parent / "fixtures/financial-expert-manifest.json"


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
    assert GOLDEN_FIXTURE_PATH.exists(), "Golden fixture must exist"
    golden = json.loads(GOLDEN_FIXTURE_PATH.read_text())

    # Strict full comparison per Luna Section 12 requirement
    assert manifest == golden, "Compiled manifest must strictly equal golden fixture"


def test_golden_manifest_tampering_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Luna Section 12 regression test: tampering with golden must fail verification."""
    original_read_text = Path.read_text

    def altered_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        contents = original_read_text(self, *args, **kwargs)
        if self.name == "financial-expert-manifest.json":
            body = json.loads(contents)
            body["pack"]["method_units"][0]["contract"]["acceptance"] = []
            return json.dumps(body)
        return contents

    monkeypatch.setattr(Path, "read_text", altered_read_text)
    with pytest.raises(AssertionError):
        test_financial_expert_manifest_compilation_and_readonly_fixture()


def _run_in_knowbench(script: str) -> subprocess.CompletedProcess[str]:
    """Execute validation script in isolated knowbench runtime."""
    clean_env = dict(os.environ)
    clean_env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        ["uv", "run", "python", "-c", script],
        cwd=KNOWBENCH_APP_DIR,
        capture_output=True,
        text=True,
        check=False,
        env=clean_env,
    )


def test_negative_two_step_plan_skipping_analysis_is_rejected() -> None:
    """Luna finding: A 2-step plan skipping analysis must NOT pass validation."""
    runner_script = f"""
import json
from pathlib import Path
from app.domain.workbench.method_catalog import ReferencedMethodCatalog
from app.domain.workbench.method_plan import (
    validate_plan,
    MethodPlan,
    PlannedStep,
    InputBinding,
)
from app.domain.workbench.errors import WorkbenchError

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])

bad_proposal = MethodPlan(
    contract="knowbench/method-plan/v1",
    parent_version=0,
    reason="attempting to skip intermediate financial analysis steps",
    steps=(
        PlannedStep(
            instance_id="step_intake",
            method_id="financial_data_intake",
            inputs=(),
            tools=(),
            reason="intake only",
        ),
        PlannedStep(
            instance_id="step_final",
            method_id="comprehensive_dossier_formatting",
            inputs=(
                InputBinding(
                    slot="financial_data_intake",
                    instance_id="step_intake",
                ),
            ),
            tools=(),
            reason="trying to directly assemble dossier without analysis",
        ),
    ),
    final_outputs={{"comprehensive_dossier_formatting": "step_final"}},
)

try:
    validate_plan(
        catalog,
        bad_proposal,
        previous=None,
        protected_prefix=0,
        completed_prefix=0,
        results={{}},
        available_tools=set(),
    )
    print("UNEXPECTED_SUCCESS")
except (WorkbenchError, ValueError) as exc:
    print(f"EXPECTED_REJECTION: {{exc}}")
"""
    res = _run_in_knowbench(runner_script)
    assert res.returncode == 0, f"Runner failed unexpectedly: {res.stderr}"
    assert "EXPECTED_REJECTION" in res.stdout
    assert "method inputs do not match the author slots" in res.stdout


def test_positive_full_analysis_dag_plan_passes_validation() -> None:
    """Full analysis DAG fulfilling M20 required inputs passes plan validation."""
    runner_script = f"""
import json
from pathlib import Path
from app.domain.workbench.method_catalog import ReferencedMethodCatalog
from app.domain.workbench.method_plan import (
    validate_plan,
    MethodPlan,
    PlannedStep,
    InputBinding,
)

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])

valid_steps = (
    PlannedStep(
        instance_id="step_intake",
        method_id="financial_data_intake",
        inputs=(),
        tools=(),
        reason="intake",
    ),
    PlannedStep(
        instance_id="step_m01",
        method_id="fin_statement_restructure",
        inputs=(
            InputBinding(
                slot="financial_data_intake", instance_id="step_intake"
            ),
        ),
        tools=(),
        reason="m01",
    ),
    PlannedStep(
        instance_id="step_m02",
        method_id="accounting_statement_reconciliation",
        inputs=(
            InputBinding(
                slot="fin_statement_restructure", instance_id="step_m01"
            ),
        ),
        tools=(),
        reason="m02",
    ),
    PlannedStep(
        instance_id="step_m03",
        method_id="non_recurring_items_purification",
        inputs=(
            InputBinding(
                slot="accounting_statement_reconciliation",
                instance_id="step_m02",
            ),
        ),
        tools=(),
        reason="m03",
    ),
    PlannedStep(
        instance_id="step_m04",
        method_id="dupont_five_way_decomposition",
        inputs=(
            InputBinding(
                slot="non_recurring_items_purification",
                instance_id="step_m03",
            ),
        ),
        tools=(),
        reason="m04",
    ),
    PlannedStep(
        instance_id="step_m05",
        method_id="solvency_liquidity_stress_check",
        inputs=(
            InputBinding(
                slot="accounting_statement_reconciliation",
                instance_id="step_m02",
            ),
        ),
        tools=(),
        reason="m05",
    ),
    PlannedStep(
        instance_id="step_m06",
        method_id="working_capital_cash_cycle_analysis",
        inputs=(
            InputBinding(
                slot="accounting_statement_reconciliation",
                instance_id="step_m02",
            ),
        ),
        tools=(),
        reason="m06",
    ),
    PlannedStep(
        instance_id="step_m07",
        method_id="earnings_quality_accrual_audit",
        inputs=(
            InputBinding(
                slot="non_recurring_items_purification",
                instance_id="step_m03",
            ),
        ),
        tools=(),
        reason="m07",
    ),
    PlannedStep(
        instance_id="step_m13",
        method_id="three_statement_linked_forecasting",
        inputs=(
            InputBinding(
                slot="dupont_five_way_decomposition", instance_id="step_m04"
            ),
            InputBinding(
                slot="working_capital_cash_cycle_analysis",
                instance_id="step_m06",
            ),
        ),
        tools=(),
        reason="m13",
    ),
    PlannedStep(
        instance_id="step_m14",
        method_id="dcf_intrinsic_valuation",
        inputs=(
            InputBinding(
                slot="three_statement_linked_forecasting",
                instance_id="step_m13",
            ),
        ),
        tools=(),
        reason="m14",
    ),
    PlannedStep(
        instance_id="step_m20",
        method_id="comprehensive_dossier_formatting",
        inputs=(
            InputBinding(
                slot="accounting_statement_reconciliation",
                instance_id="step_m02",
            ),
            InputBinding(
                slot="dupont_five_way_decomposition", instance_id="step_m04"
            ),
            InputBinding(
                slot="solvency_liquidity_stress_check",
                instance_id="step_m05",
            ),
            InputBinding(
                slot="earnings_quality_accrual_audit",
                instance_id="step_m07",
            ),
            InputBinding(
                slot="dcf_intrinsic_valuation", instance_id="step_m14"
            ),
        ),
        tools=(),
        reason="m20",
    ),
)
valid_proposal = MethodPlan(
    contract="knowbench/method-plan/v1",
    parent_version=0,
    reason="valid full analysis plan",
    steps=valid_steps,
    final_outputs={{"comprehensive_dossier_formatting": "step_m20"}},
)
validate_plan(
    catalog,
    valid_proposal,
    previous=None,
    protected_prefix=0,
    completed_prefix=0,
    results={{}},
    available_tools=set(),
)
print("VALID_DAG_PLAN_PASSED")
"""
    res = _run_in_knowbench(runner_script)
    assert res.returncode == 0, f"Runner failed unexpectedly: {res.stderr}"
    assert "VALID_DAG_PLAN_PASSED" in res.stdout
