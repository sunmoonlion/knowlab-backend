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
M03 = "non_recurring_items_purification"
M04 = "dupont_five_way_decomposition"
M05 = "solvency_liquidity_stress_check"
M06 = "working_capital_cash_cycle_analysis"
M07 = "earnings_quality_accrual_audit"
M08 = "segment_volume_price_decomposition"
M09 = "customer_supplier_concentration_scan"
M10 = "capex_capacity_cycle_tracker"
M11 = "rnd_capitalization_efficiency_eval"
M12 = "historical_cagr_trend_extrapolation"
M13 = "three_statement_linked_forecasting"
M14 = "dcf_intrinsic_valuation"
M15 = "relative_multiple_valuation"
M16 = "pledge_reduction_related_transaction_scan"
M17 = "goodwill_asset_impairment_stress_test"
M18 = "kam_contingent_liability_audit"
M19 = "competitive_moat_thesis_synthesis"
M20 = "comprehensive_dossier_formatting"


def expression_of(unit, path):
    return next(
        r.expression
        for r in unit.acceptance
        if r.kind == "computed" and r.path == path
    )


def each_has_values(unit, path):
    return next(
        r
        for r in unit.acceptance
        if r.kind == "each_has" and r.path == path
    ).values


def fact(e, value):
    address = f"fact:00000000-0000-0000-0000-{len(e.numbers) + 1:012d}"
    e.numbers[address] = Decimal(str(value))
    return address


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

    out = {
        "purified_net_profit": e.compute(
            expression_of(units[M03], "purified_net_profit"),
            net_profit=110,
            non_recurring_net=10,
        )
    }
    out["recurring_profit_margin"] = e.compute(
        expression_of(units[M03], "recurring_profit_margin"),
        purified_net_profit=out["purified_net_profit"]["value"],
        revenue=1000,
    )
    out["quarterly_unpacked_profits"] = [
        {"quarter": q, "value": v, "address": fact(e, v)}
        for q, v in (("2025Q1", 20), ("2025Q2", 30))
    ]
    all_outputs[M03] = out

    ratio = e.compute(
        expression_of(units[M05], "current_ratio"),
        current_assets=200,
        current_liabilities=100,
    )
    quick = e.compute(
        expression_of(units[M05], "quick_ratio"),
        current_assets=200,
        current_liabilities=100,
        inventory=50,
    )
    out = {
        "current_ratio": ratio,
        "quick_ratio": quick,
        "cash_to_current_debt": 0.8,
        "altman_z_score": 2.2,
        "solvency_risk_level": "中",
        "evidence_items": [
            {"metric": name, **e.compute(x=value)}
            for name, value in zip(
                each_has_values(units[M05], "evidence_items"),
                (2.0, 1.5, 0.8, 2.2),
                strict=True,
            )
        ],
    }
    all_outputs[M05] = out

    out = {
        "dio_days": 90,
        "dso_days": 60,
        "dpo_days": 45,
        "working_capital_turnover": 4.2,
    }
    out["cash_conversion_cycle_days"] = e.compute(
        expression_of(units[M06], "cash_conversion_cycle_days"),
        dio_days=out["dio_days"],
        dso_days=out["dso_days"],
        dpo_days=out["dpo_days"],
    )
    all_outputs[M06] = out

    cf_ratio = e.compute(
        expression_of(units[M07], "operating_cf_to_net_income"),
        operating_cash_flow=120,
        net_income=100,
    )
    fcf = e.compute(
        expression_of(units[M07], "free_cash_flow_firm"),
        operating_cash_flow=120,
        capex=40,
    )
    out = {
        "operating_cf_to_net_income": cf_ratio,
        "free_cash_flow_firm": fcf,
        "accrual_anomaly_score": 0.02,
        "earnings_quality_flag": "正常",
        "evidence_items": [
            {"metric": name, **e.compute(x=value)}
            for name, value in zip(
                each_has_values(units[M07], "evidence_items"),
                (1.2, 80, 0.02),
                strict=True,
            )
        ],
    }
    all_outputs[M07] = out

    out = {
        "segment_revenue_shares": [
            {"segment": "合成主业", "value": 60, "address": fact(e, 60)},
            {"segment": "合成新业务", "value": 40, "address": fact(e, 40)},
        ],
        "volume_growth_rates": [{"segment": "合成主业", "rate": 0.1}],
        "price_variance_effects": [{"segment": "合成主业", "effect": 5}],
    }
    all_outputs[M08] = out

    out = {
        "top5_customer_ratio": {"value": 0.32, "address": fact(e, "0.32")},
        "top5_supplier_ratio": 0.41,
        "concentration_risk_rating": "中",
    }
    all_outputs[M09] = out

    out = {
        "capex_to_depreciation": e.compute(
            expression_of(units[M10], "capex_to_depreciation"),
            capex=80,
            depreciation_amortization=40,
        ),
        "cip_turnover_rate": 0.6,
        "capacity_expansion_stage": "扩产中期",
    }
    all_outputs[M10] = out

    out = {
        "rnd_intensity": e.compute(
            expression_of(units[M11], "rnd_intensity"),
            rnd_total_expenditure=50,
            revenue=1000,
        ),
        "rnd_revenue_conversion": 0.8,
    }
    out["capitalization_ratio"] = e.compute(
        expression_of(units[M11], "capitalization_ratio"),
        rnd_capitalized=10,
        rnd_total_expenditure=50,
    )
    all_outputs[M11] = out

    out = {
        "revenue_cagr_3yr": e.compute(
            expression_of(units[M12], "revenue_cagr_3yr"),
            end_revenue=133.1,
            start_revenue=100,
        ),
        "net_profit_cagr_3yr": 0.08,
        "trend_projection_series": [
            {"period": "2022", "value": 100},
            {"period": "2023", "value": 115},
        ],
    }
    all_outputs[M12] = out

    equity = e.compute(
        expression_of(units[M14], "equity_value_dcf"),
        enterprise_value_dcf=1000,
        net_debt=200,
    )
    per_share = e.compute(
        expression_of(units[M14], "per_share_intrinsic_value"),
        equity_value_dcf=equity["value"],
        shares_outstanding=100,
    )
    out = {
        "enterprise_value_dcf": 1000,
        "equity_value_dcf": equity,
        "per_share_intrinsic_value": per_share,
        "wacc_used": 0.09,
        "sensitivity_table": [
            {"wacc": w, "growth": g, "per_share": 8}
            for w in (0.08, 0.09, 0.10)
            for g in (0.01, 0.02, 0.03)
        ],
        "evidence_items": [
            {"metric": name, **e.compute(x=value)}
            for name, value in zip(
                each_has_values(units[M14], "evidence_items"),
                (1000, 800, 8, 0.09),
                strict=True,
            )
        ],
    }
    all_outputs[M14] = out

    out = {
        "target_pe_ttm": 15,
        "target_pb_lf": 2.1,
        "historical_pe_percentile": 0.35,
        "implied_market_cap": e.compute(
            expression_of(units[M15], "implied_market_cap"),
            purified_net_profit=100,
            target_pe_ttm=15,
        ),
    }
    all_outputs[M15] = out

    out = {
        "controlling_shareholder_pledge_ratio": {
            "value": 0.31,
            "address": fact(e, "0.31"),
        },
        "major_holder_reduction_plans": [{"holder": "合成大股东", "shares": 100}],
        "related_party_anomaly_score": 2,
    }
    all_outputs[M16] = out

    headroom_left = e.compute(x=70)
    headroom_right = e.compute(x=70.005)
    out = {
        "goodwill_to_net_assets": e.compute(
            expression_of(units[M17], "goodwill_to_net_assets"),
            goodwill_balance=30,
            net_assets=100,
        ),
        "cgu_recoverable_amount": 500,
        "impairment_stress_headroom": {
            "left_id": headroom_left["compute_id"],
            "right_id": headroom_right["compute_id"],
        },
    }
    all_outputs[M17] = out

    out = {
        "moat_rating": "强",
        "roic_vs_wacc_spread": e.compute(
            expression_of(units[M19], "roic_vs_wacc_spread"),
            roic=0.15,
            wacc=0.09,
        ),
        "thesis_verification_matrix": [
            {"source": "无形资产", "evidence": "合成证据"}
        ],
    }
    all_outputs[M19] = out

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

    bad = deepcopy(good[M03])
    bad["quarterly_unpacked_profits"] = []
    await expect("empty:quarterly_unpacked_profits", M03, bad, False,
                 "quarterly_unpacked_profits")
    bad = deepcopy(good[M03])
    bad["quarterly_unpacked_profits"][0]["value"] += 99
    await expect("unsourced:quarterly_unpacked_profits", M03, bad, False,
                 "quarterly_unpacked_profits")
    bad = deepcopy(good[M03])
    bad["purified_net_profit"]["value"] += 99
    await expect("tampered:purified_net_profit", M03, bad, False,
                 "purified_net_profit")

    bad = deepcopy(good[M05])
    bad["evidence_items"] = bad["evidence_items"][:3]
    await expect("short:evidence_items", M05, bad, False, "evidence_items")
    bad = deepcopy(good[M05])
    bad["current_ratio"]["value"] += 99
    await expect("tampered:current_ratio", M05, bad, False, "current_ratio")
    bad = deepcopy(good[M05])
    bad["solvency_risk_level"] = " "
    await expect("blank:solvency_risk_level", M05, bad, False,
                 "solvency_risk_level")

    bad = deepcopy(good[M06])
    bad["cash_conversion_cycle_days"]["value"] += 99
    await expect("tampered:cash_conversion_cycle_days", M06, bad, False,
                 "cash_conversion_cycle_days")
    bad = deepcopy(good[M06])
    bad["cash_conversion_cycle_days"] = e.compute(
        "dio_days + dso_days", dio_days=90, dso_days=60
    )
    await expect("wrong_expression:cash_conversion_cycle_days", M06, bad, False,
                 "cash_conversion_cycle_days")

    bad = deepcopy(good[M07])
    bad["free_cash_flow_firm"]["value"] += 99
    await expect("tampered:free_cash_flow_firm", M07, bad, False,
                 "free_cash_flow_firm")
    bad = deepcopy(good[M07])
    bad["evidence_items"].append(deepcopy(bad["evidence_items"][0]))
    await expect("duplicate:evidence_items", M07, bad, False, "evidence_items")
    bad = deepcopy(good[M07])
    bad["earnings_quality_flag"] = " "
    await expect("blank:earnings_quality_flag", M07, bad, False,
                 "earnings_quality_flag")

    bad = deepcopy(good[M08])
    bad["segment_revenue_shares"] = []
    await expect("empty:segment_revenue_shares", M08, bad, False,
                 "segment_revenue_shares")
    bad = deepcopy(good[M08])
    bad["segment_revenue_shares"][0]["value"] += 99
    await expect("unsourced:segment_revenue_shares", M08, bad, False,
                 "segment_revenue_shares")

    bad = deepcopy(good[M09])
    bad["top5_customer_ratio"]["value"] += 99
    await expect("unsourced:top5_customer_ratio", M09, bad, False,
                 "top5_customer_ratio")
    bad = deepcopy(good[M09])
    bad["concentration_risk_rating"] = " "
    await expect("blank:concentration_risk_rating", M09, bad, False,
                 "concentration_risk_rating")

    bad = deepcopy(good[M10])
    bad["capex_to_depreciation"]["value"] += 99
    await expect("tampered:capex_to_depreciation", M10, bad, False,
                 "capex_to_depreciation")
    bad = deepcopy(good[M10])
    bad["capacity_expansion_stage"] = " "
    await expect("blank:capacity_expansion_stage", M10, bad, False,
                 "capacity_expansion_stage")

    bad = deepcopy(good[M11])
    bad["rnd_intensity"]["value"] += 99
    await expect("tampered:rnd_intensity", M11, bad, False, "rnd_intensity")
    bad = deepcopy(good[M11])
    bad["capitalization_ratio"]["value"] += 99
    await expect("tampered:capitalization_ratio", M11, bad, False,
                 "capitalization_ratio")

    bad = deepcopy(good[M12])
    bad["trend_projection_series"] = []
    await expect("empty:trend_projection_series", M12, bad, False,
                 "trend_projection_series")
    bad = deepcopy(good[M12])
    bad["revenue_cagr_3yr"]["value"] += 99
    await expect("tampered:revenue_cagr_3yr", M12, bad, False,
                 "revenue_cagr_3yr")

    bad = deepcopy(good[M14])
    bad["evidence_items"] = bad["evidence_items"][:3]
    await expect("short:evidence_items_dcf", M14, bad, False, "evidence_items")
    bad = deepcopy(good[M14])
    bad["equity_value_dcf"]["value"] += 99
    await expect("tampered:equity_value_dcf", M14, bad, False,
                 "equity_value_dcf")

    bad = deepcopy(good[M15])
    bad["implied_market_cap"]["value"] += 99
    await expect("tampered:implied_market_cap", M15, bad, False,
                 "implied_market_cap")
    bad = deepcopy(good[M15])
    del bad["target_pb_lf"]
    await expect("missing:target_pb_lf", M15, bad, False)

    bad = deepcopy(good[M16])
    bad["controlling_shareholder_pledge_ratio"]["value"] += 99
    await expect("unsourced:controlling_shareholder_pledge_ratio", M16, bad,
                 False, "controlling_shareholder_pledge_ratio")
    bad = deepcopy(good[M16])
    del bad["major_holder_reduction_plans"]
    await expect("missing:major_holder_reduction_plans", M16, bad, False)

    bad = deepcopy(good[M17])
    bad["impairment_stress_headroom"]["right_id"] = e.compute(x=75)[
        "compute_id"
    ]
    await expect("mismatched:impairment_stress_headroom", M17, bad, False,
                 "impairment_stress_headroom")
    bad = deepcopy(good[M17])
    bad["goodwill_to_net_assets"]["value"] += 99
    await expect("tampered:goodwill_to_net_assets", M17, bad, False,
                 "goodwill_to_net_assets")

    bad = deepcopy(good[M19])
    bad["roic_vs_wacc_spread"]["value"] += 99
    await expect("tampered:roic_vs_wacc_spread", M19, bad, False,
                 "roic_vs_wacc_spread")
    bad = deepcopy(good[M19])
    bad["moat_rating"] = " "
    await expect("blank:moat_rating", M19, bad, False, "moat_rating")
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
