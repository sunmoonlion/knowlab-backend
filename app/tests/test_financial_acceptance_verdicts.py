"""Test Financial Expert Acceptance Verdicts and Anti-Cheating Regressions.

Verifies:
- M02: Unbalanced reported difference (999999 with forged identical record IDs)
  is strictly rejected by computed diff check and reconciliation status.
- M01: Empty financial statements arrays with no records are strictly rejected
  by list_min checks.
- M18: Audit opinion with empty quotes and empty KAM matters list is strictly
  rejected by list_min and quote requirements.
- M20 covers rule:
  - Valid 1-to-1 matching against upstream dupont_evidence_items passes.
  - Missing items in evidence_audit_trail are strictly rejected.
  - Extra items not in upstream are strictly rejected.
  - Duplicate items in evidence_audit_trail are strictly rejected.
"""

import os
import subprocess
from pathlib import Path

PARENT = Path(__file__).resolve().parents[3]
KNOWBENCH_APP_DIR = (PARENT.parent / "knowbench-app/knowbench-backend/app").resolve()
GOLDEN_FIXTURE_PATH = Path(__file__).parent / "fixtures/financial-expert-manifest.json"


def _run_knowbench_judge(script: str) -> subprocess.CompletedProcess[str]:
    """Execute acceptance check script in isolated knowbench runtime."""
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


def test_m02_unbalanced_balance_sheet_difference_must_fail() -> None:
    """Luna finding: A reported 999999 diff with forged IDs must fail."""
    step = "accounting_statement_reconciliation"
    script = f"""
import asyncio
import json
from pathlib import Path
from app.application.workbench.acceptance import judge
from app.application.workbench.acceptance_numbers import judge_numbers
from app.domain.workbench.method_catalog import ReferencedMethodCatalog

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])
unit = next(u for u in catalog.method_units if u.contract.step_id == "{step}")
rules = unit.contract.acceptance

class MockRepo:
    async def list_task_events(self, task_id, **kw):
        return [
            {{"cursor": 1, "payload": {{"value": "100", "expression": "assets"}}}},
            {{"cursor": 2, "payload": {{"value": "100", "expression": "liab"}}}},
        ]
    async def list_artifacts_with_content(self, task_id):
        return []

diff_payload = {{"value": 999999, "left_id": "c1", "right_id": "c1"}}
output = {{
    "reconciliation_status": "平衡",
    "balance_sheet_balance_diff": diff_payload,
    "audit_reconciliations": [],
}}

async def main():
    verdict = await judge_numbers(
        judge(json.dumps(output), rules),
        rules,
        repo=MockRepo(),
        task={{"id": "test-task"}},
    )
    assert not verdict.ok, "Expected failure for 999999 diff"
    assert any("audit_reconciliations" in f for f in verdict.failures)
    print("VERDICT_REJECTED_AS_EXPECTED")

asyncio.run(main())
"""
    res = _run_knowbench_judge(script)
    assert res.returncode == 0, f"Judge failed unexpectedly: {res.stderr}"
    assert "VERDICT_REJECTED_AS_EXPECTED" in res.stdout


def test_m01_empty_financial_statements_must_fail() -> None:
    """Luna finding: Empty statement arrays must NOT count as analysis."""
    step = "fin_statement_restructure"
    script = f"""
import asyncio
import json
from pathlib import Path
from app.application.workbench.acceptance import judge
from app.application.workbench.acceptance_numbers import judge_numbers
from app.domain.workbench.method_catalog import ReferencedMethodCatalog

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])
unit = next(u for u in catalog.method_units if u.contract.step_id == "{step}")
rules = unit.contract.acceptance

class MockRepo:
    async def list_task_events(self, task_id, **kw):
        return []
    async def list_artifacts_with_content(self, task_id):
        return []

output = {{
    "standardized_balance_sheet": [],
    "standardized_income_statement": [],
    "standardized_cash_flow": [],
    "reclassification_audit_trail": [],
}}

async def main():
    verdict = await judge_numbers(
        judge(json.dumps(output), rules),
        rules,
        repo=MockRepo(),
        task={{"id": "test-task"}},
    )
    assert not verdict.ok, "Expected failure for empty statements"
    assert any("standardized_balance_sheet" in f for f in verdict.failures)
    print("VERDICT_REJECTED_AS_EXPECTED")

asyncio.run(main())
"""
    res = _run_knowbench_judge(script)
    assert res.returncode == 0, f"Judge failed unexpectedly: {res.stderr}"
    assert "VERDICT_REJECTED_AS_EXPECTED" in res.stdout


def test_m18_empty_kam_quotes_must_fail() -> None:
    """Luna finding: Opinion with empty quotes and 0 KAM must fail."""
    step = "kam_contingent_liability_audit"
    script = f"""
import asyncio
import json
from pathlib import Path
from app.application.workbench.acceptance import judge
from app.application.workbench.acceptance_numbers import judge_numbers
from app.domain.workbench.method_catalog import ReferencedMethodCatalog

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])
unit = next(u for u in catalog.method_units if u.contract.step_id == "{step}")
rules = unit.contract.acceptance

class MockRepo:
    async def list_task_events(self, task_id, **kw):
        return []
    async def list_artifacts_with_content(self, task_id):
        return []

output = {{
    "audit_opinion_type": {{"text": "无保留意见", "quotes": []}},
    "kam_topics_count": 0,
    "kam_matters": [],
    "external_guarantee_ratio": 0,
    "contingent_liability_risk_verdict": "低",
}}

async def main():
    verdict = await judge_numbers(
        judge(json.dumps(output), rules),
        rules,
        repo=MockRepo(),
        task={{"id": "test-task"}},
    )
    assert not verdict.ok, "Expected failure for empty kam_matters"
    assert any("kam_matters" in f for f in verdict.failures)
    print("VERDICT_REJECTED_AS_EXPECTED")

asyncio.run(main())
"""
    res = _run_knowbench_judge(script)
    assert res.returncode == 0, f"Judge failed unexpectedly: {res.stderr}"
    assert "VERDICT_REJECTED_AS_EXPECTED" in res.stdout


def test_m20_covers_rule_valid_payload_passes() -> None:
    """M20 covers rule: exact 1-to-1 match against upstream passes."""
    step = "comprehensive_dossier_formatting"
    script = f"""
import asyncio
import json
from pathlib import Path
from app.application.workbench.acceptance import judge
from app.application.workbench.acceptance_numbers import judge_numbers
from app.domain.workbench.method_catalog import ReferencedMethodCatalog

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])
unit = next(u for u in catalog.method_units if u.contract.step_id == "{step}")
rules = unit.contract.acceptance

class MockRepo:
    async def list_task_events(self, task_id, **kw):
        return []
    async def list_artifacts_with_content(self, task_id):
        return [{{
            "kind": "step",
            "name": "dupont_five_way_decomposition",
            "content": {{
                "dupont_evidence_items": [
                    "roe", "net_profit_margin", "asset_turnover",
                    "equity_multiplier", "tax_burden_ratio",
                    "interest_burden_ratio",
                ],
            }},
        }}]

output = {{
    "structured_dossier": "研究底稿",
    "executive_summary": "摘要",
    "risk_factors": ["行业竞争加剧"],
    "evidence_audit_trail": [
        {{"metric": "roe"}},
        {{"metric": "net_profit_margin"}},
        {{"metric": "asset_turnover"}},
        {{"metric": "equity_multiplier"}},
        {{"metric": "tax_burden_ratio"}},
        {{"metric": "interest_burden_ratio"}},
    ],
}}

async def main():
    verdict = await judge_numbers(
        judge(json.dumps(output), rules),
        rules,
        repo=MockRepo(),
        task={{"id": "test-task"}},
    )
    assert verdict.ok, f"Expected success, got failures: {{verdict.failures}}"
    print("VERDICT_PASSED_AS_EXPECTED")

asyncio.run(main())
"""
    res = _run_knowbench_judge(script)
    assert res.returncode == 0, f"Judge failed unexpectedly: {res.stderr}"
    assert "VERDICT_PASSED_AS_EXPECTED" in res.stdout


def test_m20_covers_rule_missing_extra_duplicate_rejected() -> None:
    """M20 covers rule: missing, extra, and duplicate items rejected."""
    step = "comprehensive_dossier_formatting"
    script = f"""
import asyncio
import json
from pathlib import Path
from app.application.workbench.acceptance import judge
from app.application.workbench.acceptance_numbers import judge_numbers
from app.domain.workbench.method_catalog import ReferencedMethodCatalog

manifest = json.loads(Path("{GOLDEN_FIXTURE_PATH}").read_text())
catalog = ReferencedMethodCatalog.model_validate(manifest["pack"])
unit = next(u for u in catalog.method_units if u.contract.step_id == "{step}")
rules = unit.contract.acceptance

class MockRepo:
    async def list_task_events(self, task_id, **kw):
        return []
    async def list_artifacts_with_content(self, task_id):
        return [{{
            "kind": "step",
            "name": "dupont_five_way_decomposition",
            "content": {{
                "dupont_evidence_items": [
                    "roe", "net_profit_margin", "asset_turnover",
                    "equity_multiplier", "tax_burden_ratio",
                    "interest_burden_ratio",
                ],
            }},
        }}]

async def run(trail):
    output = {{
        "structured_dossier": "研究底稿",
        "executive_summary": "摘要",
        "risk_factors": ["风险"],
        "evidence_audit_trail": trail,
    }}
    return await judge_numbers(
        judge(json.dumps(output), rules),
        rules,
        repo=MockRepo(),
        task={{"id": "test-task"}},
    )

async def main():
    # 1. Missing items
    v_missing = await run([{{"metric": "roe"}}, {{"metric": "net_profit_margin"}}])
    assert not v_missing.ok
    assert any("missing 'asset_turnover'" in f for f in v_missing.failures)

    # 2. Extra items
    v_extra = await run([
        {{"metric": "roe"}}, {{"metric": "net_profit_margin"}},
        {{"metric": "asset_turnover"}}, {{"metric": "equity_multiplier"}},
        {{"metric": "tax_burden_ratio"}}, {{"metric": "interest_burden_ratio"}},
        {{"metric": "spurious_metric"}},
    ])
    assert not v_extra.ok
    assert any("not upstream 'spurious_metric'" in f for f in v_extra.failures)

    # 3. Duplicate items
    v_duplicate = await run([
        {{"metric": "roe"}}, {{"metric": "roe"}},
        {{"metric": "asset_turnover"}}, {{"metric": "equity_multiplier"}},
        {{"metric": "tax_burden_ratio"}}, {{"metric": "interest_burden_ratio"}},
    ])
    assert not v_duplicate.ok
    assert any("twice 'roe'" in f for f in v_duplicate.failures)

    print("ALL_COVERS_NEGATIVE_CASES_REJECTED")

asyncio.run(main())
"""
    res = _run_knowbench_judge(script)
    assert res.returncode == 0, f"Judge failed unexpectedly: {res.stderr}"
    assert "ALL_COVERS_NEGATIVE_CASES_REJECTED" in res.stdout
