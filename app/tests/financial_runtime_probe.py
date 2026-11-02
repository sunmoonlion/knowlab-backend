"""Synthetic cross-app proof: real positive outputs are passed unchanged downstream."""

import asyncio
import json
import sys
from copy import deepcopy
from decimal import Decimal
from itertools import product
from pathlib import Path

from app.application.workbench.acceptance import judge
from app.application.workbench.acceptance_numbers import judge_numbers
from app.domain.workbench import compute as calc
from app.domain.workbench.method_catalog import ReferencedMethodCatalog

catalog = ReferencedMethodCatalog.model_validate(
    json.loads(Path(sys.argv[1]).read_text())["pack"]
)
units = {u.contract.step_id: u.contract for u in catalog.method_units}
M01 = "fin_statement_restructure"
M02 = "accounting_statement_reconciliation"
M04 = "dupont_five_way_decomposition"
M13 = "three_statement_linked_forecasting"
M18 = "kam_contingent_liability_audit"
M20 = "comprehensive_dossier_formatting"


class Evidence:
    def __init__(self):
        self.events, self.artifacts, self.numbers = [], {}, {}
        self.pages = {
            (
                "00000000-0000-0000-0000-000000000001",
                8,
            ): "无保留意见。存货跌价准备。已执行盘点测试。"
        }

    def compute(self, expression="x", **operands):
        values = {k: Decimal(str(v)) for k, v in operands.items()}
        value = calc.evaluate(expression, values)
        cursor = len(self.events) + 1
        self.events.append(
            {
                "cursor": cursor,
                "payload": {
                    "value": str(value),
                    "expression": expression,
                    "inputs": [
                        {"name": k, "value": str(v), "ref": f"fact:{cursor}_{k}"}
                        for k, v in values.items()
                    ],
                },
            }
        )
        return {"value": float(value), "compute_id": f"c{cursor}"}

    async def list_task_events(self, task_id, **kwargs):
        return self.events

    async def list_artifacts_with_content(self, task_id):
        return [
            {"kind": "step", "name": name, "content": out}
            for name, out in self.artifacts.items()
        ]

    async def facts(self, repo, task, addresses):
        return {a: self.numbers[a] for a in addresses}

    async def quotes(self, repo, task, quotes):
        return [
            {"found": q["text"] in self.pages.get((q["material_id"], q["page"]), "")}
            for q in quotes
        ]


def outputs(e):
    all_outputs = {}
    out = {
        "reclassification_audit_trail": [{"item": "口径", "reason": "合成同口径数据"}]
    }
    for path in (
        "standardized_balance_sheet",
        "standardized_income_statement",
        "standardized_cash_flow",
    ):
        out[path] = []
        for n in range(3):
            address = f"fact:00000000-0000-0000-0000-{len(e.numbers) + 1:012d}"
            e.numbers[address] = Decimal(n + 10)
            out[path].append(
                {
                    "item": str(n),
                    "period": "2024",
                    "unit": "元",
                    "value": n + 10,
                    "address": address,
                }
            )
    all_outputs[M01] = out
    out = {"reconciliation_status": "已核对"}
    params = {
        "balance_sheet_balance_diff": dict(
            total_assets=100, total_liabilities=40, equity=60
        ),
        "opening_balance_diff": dict(
            opening_assets=80, opening_liabilities=30, opening_equity=50
        ),
        "retained_earnings_diff": dict(
            closing_retained=20,
            opening_retained=10,
            parent_profit=15,
            reserve_transfer=2,
            dividends=3,
            restatement_adjustment=0,
        ),
        "cash_flow_diff": dict(closing_cash=30, opening_cash=20, cash_increase=10),
    }
    for r in units[M02].acceptance:
        if r.kind == "computed" and r.path in params:
            out[r.path] = e.compute(r.expression, **params[r.path])
    out["opening_comparisons"] = [
        {
            "item": "assets",
            "period": "2024",
            **e.compute(
                "abs(opening_value - prior_closing_value)",
                opening_value=80,
                prior_closing_value=80,
            ),
        }
    ]
    out["audit_reconciliations"] = [
        {"check_name": n, "evidence": "合成勾稽记录"}
        for n in (
            "closing_balance",
            "opening_balance",
            "retained_earnings",
            "cash_flow",
            "opening_comparisons",
        )
    ]
    all_outputs[M02] = out
    factors = dict(
        tax_burden=0.8,
        interest_burden=0.9,
        ebit_margin=0.2,
        asset_turnover=1,
        equity_multiplier=2,
    )
    out = dict(factors)
    expr = next(
        r.expression
        for r in units[M04].acceptance
        if r.kind == "computed" and r.path == "roe"
    )
    out["roe"] = e.compute(expr, **factors)
    direct = e.compute(
        "net_profit / average_equity", net_profit=28.8, average_equity=100
    )
    out["dupont_chain_product"] = {
        "left_id": out["roe"]["compute_id"],
        "right_id": direct["compute_id"],
    }
    out["dupont_evidence_items"] = [{"metric": "roe", **out["roe"]}] + [
        {"metric": k, **e.compute(x=v)} for k, v in factors.items()
    ]
    all_outputs[M04] = out
    out = {
        "forecast_assumptions": [
            {"name": "销量", "reason": "合成场景", "source": "测试假设"}
        ]
    }
    horizons, scenarios = (
        ("next_quarter", "following_quarter"),
        ("pessimistic", "base", "optimistic"),
    )
    out["forecast_income_statement"] = [
        {
            "horizon": h,
            "period": "2025Q1" if h == horizons[0] else "2025Q2",
            "scenario": scenario,
            "metric": metric,
            **e.compute(x=100),
        }
        for h, scenario, metric in product(
            horizons, scenarios, ("revenue", "parent_net_profit", "operating_cost")
        )
    ]
    for path, fields, expression in [
        (
            "forecast_balance_sheet",
            dict(assets=100, liabilities=40, equity=60),
            "abs(assets - liabilities - equity)",
        ),
        (
            "forecast_cashflow_statement",
            dict(opening_cash=20, closing_cash=30, cash_increase=10),
            "abs(closing_cash - opening_cash - cash_increase)",
        ),
    ]:
        out[path] = [
            {
                "horizon": h,
                "period": "2025Q1" if h == horizons[0] else "2025Q2",
                "scenario": scenario,
                **fields,
                **e.compute(expression, **fields),
            }
            for h, scenario in product(horizons, scenarios)
        ]
    out["forecast_intervals"] = [
        {
            "horizon": h,
            "metric": m,
            "lower": 1,
            "base": 2,
            "upper": 3,
            **e.compute("max(lower-base, base-upper, 0)", lower=1, base=2, upper=3),
        }
        for h, m in product(horizons, ("revenue", "parent_net_profit", "gross_margin"))
    ]
    all_outputs[M13] = out

    def quote(text):
        return [
            {
                "material_id": "00000000-0000-0000-0000-000000000001",
                "page": 8,
                "text": text,
            }
        ]

    all_outputs[M18] = {
        "audit_opinion_type": [
            {"kind": "opinion", "text": "无保留意见", "quotes": quote("无保留意见")}
        ],
        "kam_topics_count": 1,
        "kam_matters": [
            {
                "kind": "kam",
                "matter": "存货跌价准备",
                "audit_response": "已执行盘点测试",
                "quotes": quote("存货跌价准备。已执行盘点测试。"),
            }
        ],
        "external_guarantee_ratio": 0,
        "contingent_liability_risk_verdict": "合成资料未见担保",
    }
    e.artifacts[M04], e.artifacts[M02] = all_outputs[M04], all_outputs[M02]
    out = {
        "structured_dossier": "合成底稿",
        "executive_summary": "仅回归验证",
        "risk_factors": [
            {"text": f"合成风险 {i}", "source": "dupont"} for i in range(3)
        ],
    }
    for r in units[M20].acceptance:
        if r.kind != "covers":
            continue
        source, _, path = r.source.partition(".")
        if source not in e.artifacts:
            sr = next(
                c
                for c in units[source].acceptance
                if c.kind == "each_has" and c.path == path and c.field == "metric"
            )
            e.artifacts[source] = {
                path: [
                    {"metric": n, **e.compute(x=i + 1)} for i, n in enumerate(sr.values)
                ]
            }
        out[r.path] = [
            {
                "metric": row[r.source_field],
                "source": next(
                    c.values[0]
                    for c in units[M20].acceptance
                    if c.kind == "each_has" and c.path == r.path and c.field == "source"
                ),
            }
            for row in e.artifacts[source][path]
        ]
    all_outputs[M20] = out
    return all_outputs


async def main():
    e = Evidence()
    good = outputs(e)
    checks = {}

    async def expect(name, method, output, accepted, path=None):
        rules = units[method].acceptance
        v = await judge_numbers(
            judge(json.dumps(output, ensure_ascii=False), rules),
            rules,
            repo=e,
            task={"id": "synthetic-test"},
            reader=e,
        )
        assert v.ok == accepted, (name, v.failures, v.checks)
        if path:
            assert any(
                c.get("path") == path and c.get("pass") is False for c in v.checks
            ), (name, v.checks)
        checks[name] = True

    for method, out in good.items():
        await expect("positive:" + method, method, out, True)
    for path in (
        "standardized_balance_sheet",
        "standardized_income_statement",
        "standardized_cash_flow",
    ):
        bad = deepcopy(good[M01])
        bad[path] = []
        await expect("empty:" + path, M01, bad, False, path)
        bad = deepcopy(good[M01])
        bad[path][0]["value"] += 99
        await expect("unsourced:" + path, M01, bad, False, path)
    bad = deepcopy(good[M02])
    expr = next(
        r.expression
        for r in units[M02].acceptance
        if r.kind == "computed" and r.path == "balance_sheet_balance_diff"
    )
    bad["balance_sheet_balance_diff"] = e.compute(
        expr, total_assets=1000099, total_liabilities=40, equity=60
    )
    await expect(
        "nonzero_matching_computation", M02, bad, False, "balance_sheet_balance_diff"
    )
    bad["balance_sheet_balance_diff"] = {
        "value": 999999,
        "left_id": "c1",
        "right_id": "c1",
    }
    await expect("forged_equal_ids", M02, bad, False, "balance_sheet_balance_diff")
    for path in (
        "forecast_income_statement",
        "forecast_balance_sheet",
        "forecast_cashflow_statement",
    ):
        bad = deepcopy(good[M13])
        bad[path] = []
        await expect("empty:" + path, M13, bad, False, path)
        bad = deepcopy(good[M13])
        bad[path][-1] = deepcopy(bad[path][0])
        await expect("duplicate:" + path, M13, bad, False, path)
    bad = deepcopy(good[M13])
    row = bad["forecast_balance_sheet"][0]
    row["assets"] = 1000099
    row.update(
        e.compute(
            "abs(assets - liabilities - equity)",
            assets=1000099,
            liabilities=40,
            equity=60,
        )
    )
    await expect("nonzero_forecast", M13, bad, False, "forecast_balance_sheet")
    for path, field in [
        ("forecast_balance_sheet", "assets"),
        ("forecast_cashflow_statement", "closing_cash"),
    ]:
        bad = deepcopy(good[M13])
        bad[path][0][field] += 10
        await expect("altered_field_valid_proof:" + path, M13, bad, False, path)
    bad = deepcopy(good[M13])
    bad["forecast_intervals"][0]["lower"] = 100
    await expect(
        "inverted_interval_with_valid_zero_proof", M13, bad, False, "forecast_intervals"
    )
    for path in ("audit_opinion_type", "kam_matters"):
        for mutation in ("missing", "page_zero", "wrong_text", "wrong_kind"):
            bad = deepcopy(good[M18])
            row = bad[path][0]
            if mutation == "missing":
                row["quotes"] = []
            elif mutation == "page_zero":
                row["quotes"][0]["page"] = 0
            elif mutation == "wrong_text":
                row["quotes"][0]["text"] = "不存在的引文"
            else:
                row["kind"] = "skip"
            await expect(mutation + ":" + path, M18, bad, False, path)
    for path in (
        "evidence_audit_trail",
        "reconciliation_evidence",
        "solvency_evidence",
        "earnings_evidence",
        "valuation_evidence",
    ):
        for mutation in ("missing", "extra", "duplicate", "blank_source"):
            bad = deepcopy(good[M20])
            if mutation == "missing":
                bad[path].pop()
            elif mutation == "extra":
                bad[path].append({"metric": "invented", "source": "invented"})
            elif mutation == "duplicate":
                bad[path].append(deepcopy(bad[path][0]))
            else:
                bad[path][0]["source"] = " "
            await expect(mutation + ":" + path, M20, bad, False, path)
    bad = deepcopy(good[M20])
    bad["risk_factors"] = [{"text": " ", "source": ""}] * 3
    await expect("blank_risks", M20, bad, False, "risk_factors")
    bad = deepcopy(good[M20])
    bad["risk_factors"][0]["source"] = "missing_method"
    await expect("invented_risk_source", M20, bad, False, "risk_factors")
    print(json.dumps(checks))


if __name__ == "__main__":
    asyncio.run(main())
