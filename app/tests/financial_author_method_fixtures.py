"""Synthetic Author Method Fixtures for Financial Analysis and Valuation (20 Methods).

=============================================================================
SYNTHETIC TEST FIXTURE
依据《方法库并行开发要求-Luna交接.md》第 10、11、12 节要求：
1. 测试资料明确标记为合成夹具（Synthetic Fixture），业务口径待真实作者确认；
2. 彻底纠偏假接入：废除统一依赖 fake intake 的单点架构，建立真实的业务 DAG；
3. 成稿方法（M20）真实消费各分析步骤的结构化产物，杜绝跳过分析步骤两步直通漏洞；
4. covers 规则要求上游为列表、下游为含对应 field 的对象列表，覆盖 1-to-1 语义；
5. 真实覆盖 computed/identity/sourced/quoted/covers/list_min/each_has 等多层级质检验收。
=============================================================================
"""

from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.domain.knowbots.compile import compile_manifest
from app.domain.knowbots.methods import (
    BindMethod,
    FreezeMethod,
    MethodDefinition,
    _input_schema,
    bind,
    freeze,
)
from app.domain.knowbots.workdraft import (
    CheckCell,
    MethodSelection,
    OutputCell,
    OutputField,
    StepCell,
    WorkdraftBody,
    cells,
)

SYNTHETIC_DATA_FLAG = True

# ---------------------------------------------------------------------------
# 20 个专家方法规范（包含真实业务 DAG 依赖与严格证据验收规则）
# ---------------------------------------------------------------------------


def rows(output_name: str, **fields: Literal["text", "number"]) -> OutputCell:
    return OutputCell(
        name=output_name,
        type="table",
        items=tuple(OutputField(name=key, type=kind) for key, kind in fields.items()),
    )


def check(template: str, path: str, **params: Any) -> CheckCell:
    return CheckCell.model_validate(
        {"template": template, "params": {"path": path, **params}}
    )


def required(*names: str) -> CheckCell:
    return CheckCell(template="required", params={"keys": list(names)})


def financial_rows_checks(path: str, minimum: int = 3) -> list[CheckCell]:
    return [
        check("list_min", path, min=minimum),
        check(
            "each_has",
            path,
            keys=["item", "period", "unit", "value", "address"],
            non_empty_keys=["item", "period", "unit"],
            unique_by=["period", "item"],
        ),
        check("sourced", path, tolerance=0.01),
    ]


def forecast_checks(path: str, metrics: list[str]) -> list[CheckCell]:
    return [
        check("list_min", path, min=6 * len(metrics)),
        check(
            "each_has",
            path,
            keys=["horizon", "period", "scenario", "metric", "value", "compute_id"],
            non_empty_keys=["period"],
            unique_by=["horizon", "scenario", "metric"],
            field="horizon",
            values=["next_quarter", "following_quarter"],
        ),
        check(
            "each_has",
            path,
            field="scenario",
            values=["pessimistic", "base", "optimistic"],
        ),
        check("each_has", path, field="metric", values=metrics),
        check("computed", path, tolerance=0.01),
    ]


def forecast_balance_checks(
    path: str, fields: list[str], expression: str
) -> list[CheckCell]:
    return [
        check("list_min", path, min=6),
        check(
            "each_has",
            path,
            keys=["horizon", "period", "scenario", *fields, "value", "compute_id"],
            non_empty_keys=["period"],
            unique_by=["horizon", "scenario"],
            field="horizon",
            values=["next_quarter", "following_quarter"],
        ),
        check(
            "each_has",
            path,
            field="scenario",
            values=["pessimistic", "base", "optimistic"],
        ),
        check(
            "computed",
            path,
            expression=expression,
            input_keys=fields,
            expected_value=0,
            tolerance=0.01,
        ),
    ]


ALL_20_SPECS: list[dict[str, Any]] = [
    # M01: 财务三表标准化重构与口径对齐
    {
        "id": "fin_statement_restructure",
        "title": "财务三表标准化重构与口径对齐",
        "category": "accounting",
        "tags": ["三表标准化", "口径对齐", "CAS2024"],
        "does": (
            "读取原始披露三表，按现行一般企业财务报表格式重构列报科目，"
            "全部金额统一换算为元，逐项记录重分类调整溯源；"
            "输出供下游勾稽与分析直接消费的标准化三表。"
        ),
        "why": "消除非标科目与列报口径差异，形成横向与跨期可比的基础分析底稿。",
        "provenance": (
            "财政部《企业会计准则第30号——财务报表列报》（2014年修订）及"
            "一般企业财务报表格式通知（财会〔2019〕6号）"
        ),
        "does_not": (
            "不接数据真实性核验（属勾稽与审计排查），不接合并抵销调整"
            "（原始披露已是合并口径）；金融企业专用报表格式未覆盖。"
        ),
        "elsewhere": (
            "勾稽平衡校验找「报表勾稽平衡与重述校验」；"
            "原始数据采集接入属平台数据接入层。"
        ),
        "feeder_outputs": {
            "raw_data": [
                OutputCell(name="raw_balance_sheet", type="table"),
                OutputCell(name="raw_income_statement", type="table"),
                OutputCell(name="raw_cash_flow_statement", type="table"),
            ]
        },
        "inputs": {"feeder_raw": "raw_data"},
        "expert_inputs": {"raw_data": "financial_data_intake"},
        "outputs": [
            rows("standardized_balance_sheet", item="text", period="text", unit="text"),
            rows(
                "standardized_income_statement", item="text", period="text", unit="text"
            ),
            rows("standardized_cash_flow", item="text", period="text", unit="text"),
            rows("reclassification_audit_trail", item="text", reason="text"),
        ],
        "checks": [
            required(
                "standardized_balance_sheet",
                "standardized_income_statement",
                "standardized_cash_flow",
                "reclassification_audit_trail",
            ),
            *financial_rows_checks("standardized_balance_sheet"),
            *financial_rows_checks("standardized_income_statement"),
            *financial_rows_checks("standardized_cash_flow"),
            check("list_min", "reclassification_audit_trail", min=1),
            check(
                "each_has",
                "reclassification_audit_trail",
                keys=["item", "reason"],
                non_empty_keys=["item", "reason"],
            ),
        ],
    },
    # M02: 报表勾稽平衡与重述校验
    {
        "id": "accounting_statement_reconciliation",
        "title": "报表勾稽平衡与重述校验",
        "category": "accounting",
        "tags": ["勾稽平衡", "资产平表", "追溯重述"],
        "does": (
            "对标准化三表执行四层勾稽校验：①资产=负债+所有者权益，期末与期初分别校验，"
            "尾差绝对值<1.00元（数据一律经标准化层换算为元）；"
            "②期末未分配利润=期初未分配利润+本期归母净利润−提取盈余公积−应付股利"
            "±追溯调整，各项取数自所有者权益变动表（增发缴款计入股本与资本公积，"
            "不进本等式）；"
            "③现金流量表净增加额=现金及现金等价物期末余额减期初余额；"
            "④本期报表期初数与上年期末数按披露行项目全量逐项比对，"
            "不一致即识别为追溯重述并记录披露出处。"
        ),
        "why": (
            "验证三表横向纵向强勾稽平衡，排查会计不平衡或重述差错，建立分析可信基石。"
        ),
        "provenance": (
            "《企业会计准则——基本准则》会计等式；中国注册会计师审计准则第1313号"
            "《分析程序》；证监会《公开发行证券的公司信息披露编报规则第19号"
            "——财务信息的更正及相关披露》（追溯重述部分）"
        ),
        "does_not": (
            "不接科目口径调整与重分类（前置方法的事），不接审计意见真伪鉴定。"
        ),
        "elsewhere": (
            "科目重分类与报表标准化找「财务三表标准化重构与口径对齐」；"
            "审计意见与关键审计事项找「关键审计事项与或有负债排查」。"
        ),
        "feeder_outputs": {
            "statements": [
                OutputCell(name="standardized_balance_sheet", type="table"),
                OutputCell(name="standardized_income_statement", type="table"),
                OutputCell(name="standardized_cash_flow", type="table"),
                OutputCell(name="reclassification_audit_trail", type="list"),
            ]
        },
        "inputs": {"feeder_statements": "statements"},
        "expert_inputs": {"statements": "fin_statement_restructure"},
        "outputs": [
            OutputCell(name="reconciliation_status", type="text"),
            OutputCell(name="balance_sheet_balance_diff", type="number"),
            OutputCell(name="opening_balance_diff", type="number"),
            OutputCell(name="retained_earnings_diff", type="number"),
            OutputCell(name="cash_flow_diff", type="number"),
            rows("opening_comparisons", item="text", period="text"),
            rows("audit_reconciliations", check_name="text", evidence="text"),
        ],
        "checks": [
            required(
                "reconciliation_status",
                "balance_sheet_balance_diff",
                "opening_balance_diff",
                "retained_earnings_diff",
                "cash_flow_diff",
                "opening_comparisons",
                "audit_reconciliations",
            ),
            check("non_empty", "reconciliation_status"),
            check(
                "computed",
                "balance_sheet_balance_diff",
                expression="abs(total_assets - (total_liabilities + equity))",
                expected_value=0,
                tolerance=1,
            ),
            check(
                "computed",
                "opening_balance_diff",
                expression=(
                    "abs(opening_assets - (opening_liabilities + opening_equity))"
                ),
                expected_value=0,
                tolerance=1,
            ),
            check(
                "computed",
                "retained_earnings_diff",
                expression=(
                    "abs(closing_retained - (opening_retained + parent_profit "
                    "- reserve_transfer - dividends + restatement_adjustment))"
                ),
                expected_value=0,
                tolerance=1,
            ),
            check(
                "computed",
                "cash_flow_diff",
                expression="abs(closing_cash - opening_cash - cash_increase)",
                expected_value=0,
                tolerance=1,
            ),
            check("list_min", "opening_comparisons", min=1),
            check(
                "each_has",
                "opening_comparisons",
                keys=["item", "period"],
                non_empty_keys=["item", "period"],
                unique_by=["item", "period"],
            ),
            check(
                "computed",
                "opening_comparisons",
                expression="abs(opening_value - prior_closing_value)",
                expected_value=0,
                tolerance=1,
            ),
            check("list_min", "audit_reconciliations", min=5),
            check(
                "each_has",
                "audit_reconciliations",
                keys=["check_name", "evidence"],
                non_empty_keys=["evidence"],
                unique_by=["check_name"],
                field="check_name",
                values=[
                    "closing_balance",
                    "opening_balance",
                    "retained_earnings",
                    "cash_flow",
                    "opening_comparisons",
                ],
            ),
        ],
    },
    # M03: 非经常性损益穿透与扣非还原 (含单季还原)
    {
        "id": "non_recurring_items_purification",
        "title": "非经常性损益穿透与扣非还原",
        "category": "profitability",
        "tags": ["扣非净利润", "非经常损益", "单季还原", "核心盈利"],
        "does": (
            "按证监会非经常性损益定义逐项剥离政府补助、资产处置损益、公允价值变动"
            "等非经常项目，还原扣非净利润；季报为累计披露，按差分法还原单季度"
            "（Q1=一季报，Q2=半年报−Q1，Q3=前三季−半年报，Q4=年报−前三季），"
            "还原结果与公司披露的单季数据互核。"
        ),
        "why": (
            "剔除一次性与非经营偶发扰动，识别企业不可持续的利润粉饰，"
            "核准主业真实造血能力。"
        ),
        "provenance": (
            "中国证监会《公开发行证券的公司信息披露解释性公告第1号——非经常性损益》"
            "（2008年发布、2023年修订）；A股季报累计披露制度下的差分还原惯例"
        ),
        "does_not": (
            "不接非经常性损益认定口径的再裁量（以公告列举为准，列举外项目标存疑"
            "列入待确认）；不接年化处理与季节性调整。"
        ),
        "elsewhere": (
            "趋势外推找「复合年均增长率与趋势外推」；盈利结构分解找「杜邦五步分解法」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="purified_net_profit", type="number"),
            OutputCell(name="recurring_profit_margin", type="number"),
            OutputCell(name="quarterly_unpacked_profits", type="list"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "purified_net_profit",
                        "recurring_profit_margin",
                        "quarterly_unpacked_profits",
                    ]
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "purified_net_profit",
                    "expression": "net_profit - non_recurring_net",
                    "tolerance": 0.01,
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "recurring_profit_margin",
                    "expression": "purified_net_profit / revenue",
                    "tolerance": 0.0001,
                },
            ),
            CheckCell(
                template="list_min",
                params={"path": "quarterly_unpacked_profits", "min": 1},
            ),
            CheckCell(
                template="sourced",
                params={"path": "quarterly_unpacked_profits", "tolerance": 0.01},
            ),
        ],
    },
    # M04: 杜邦五步分解法
    {
        "id": "dupont_five_way_decomposition",
        "title": "杜邦五步分解法",
        "category": "profitability",
        "tags": ["杜邦分析", "ROE分解", "杠杆乘数", "周转率"],
        "does": (
            "将净资产收益率(ROE)分解为五因子乘积，净利润一律指扣非净利润"
            "（接「非经常性损益穿透与扣非还原」产物）：税收负担率(扣非净利润/税前利润)"
            "×利息负担率(税前利润/息税前利润)×息税前利润率(EBIT/营业收入)"
            "×资产周转率(营业收入/平均总资产)×权益乘数(平均总资产/平均净资产)；"
            "资产与净资产取期初期末算术平均，首个报告期无期初数时退回期末值并在证据项标注；"
            "逐因子给出取值来源与计算记录编号，乘积与ROE直接值互核。"
        ),
        "why": (
            "结构化解构盈利源动力，精准厘清ROE变动究竟由主营盈利、"
            "财务杠杆还是运营周转所驱动。"
        ),
        "provenance": (
            "杜邦公司财务分析体系（Donaldson Brown, 1914）的五因子扩展；"
            "CFA Institute《Financial Reporting and Analysis》杜邦分解框架"
        ),
        "does_not": (
            "不接银行、保险、券商等金融企业——其报表结构与监管口径不适用通用"
            "杜邦分解，金融版分解另列候选方法，不在本方法内打补丁。"
        ),
        "elsewhere": (
            "金融企业盈利结构分析需另行指定方法；估值找「企业自由现金流贴现估值 DCF」。"
        ),
        "feeder_outputs": {
            "purified": [
                OutputCell(name="purified_net_profit", type="number"),
                OutputCell(name="recurring_profit_margin", type="number"),
                OutputCell(name="quarterly_unpacked_profits", type="list"),
            ]
        },
        "inputs": {"feeder_purified": "purified"},
        "expert_inputs": {"purified": "non_recurring_items_purification"},
        "outputs": [
            OutputCell(name="roe", type="number"),
            OutputCell(name="dupont_chain_product", type="number"),
            OutputCell(name="tax_burden", type="number"),
            OutputCell(name="interest_burden", type="number"),
            OutputCell(name="ebit_margin", type="number"),
            OutputCell(name="asset_turnover", type="number"),
            OutputCell(name="equity_multiplier", type="number"),
            rows(
                "dupont_evidence_items",
                metric="text",
                value="number",
                compute_id="text",
            ),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "roe",
                        "dupont_chain_product",
                        "tax_burden",
                        "interest_burden",
                        "ebit_margin",
                        "asset_turnover",
                        "equity_multiplier",
                        "dupont_evidence_items",
                    ]
                },
            ),
            CheckCell(
                template="list_min",
                params={"path": "dupont_evidence_items", "min": 6},
            ),
            check(
                "each_has",
                "dupont_evidence_items",
                keys=["metric", "value", "compute_id"],
                unique_by=["metric"],
                field="metric",
                values=[
                    "roe",
                    "tax_burden",
                    "interest_burden",
                    "ebit_margin",
                    "asset_turnover",
                    "equity_multiplier",
                ],
            ),
            check("computed", "dupont_evidence_items", tolerance=0.0001),
            CheckCell(
                template="computed",
                params={
                    "path": "roe",
                    "expression": (
                        "tax_burden * interest_burden * ebit_margin * "
                        "asset_turnover * equity_multiplier"
                    ),
                    "tolerance": 0.0001,
                },
            ),
            CheckCell(
                template="identity",
                params={"path": "dupont_chain_product", "tolerance": 0.001},
            ),
        ],
    },
    # M05: 偿债压力与流动性体检
    {
        "id": "solvency_liquidity_stress_check",
        "title": "偿债压力与流动性体检",
        "category": "solvency",
        "tags": ["流动比率", "速动比率", "现金短债比", "Altman-Z"],
        "does": (
            "计算流动比率、速动比率、现金短债比与Altman Z-Score，"
            "推演极端承压情景下的流动性缺口，并按Altman原始分区给出偿债风险分级"
            "（Z>2.99低、1.81–2.99中、<1.81高）。"
        ),
        "why": (
            "全面评估短期偿债缓冲与长期资本结构稳健性，"
            "提前预警债务违约与资金链断裂断崖风险。"
        ),
        "provenance": (
            "Altman（1968）Z-Score初始模型（制造业上市公司样本）；"
            "流动/速动/现金短债比为银行信贷与信用评级通行口径"
        ),
        "does_not": (
            "不接金融机构偿债分析（监管资本口径另行处理）；Z-Score沿用1968年"
            "初始模型与阈值，未按行业校准，分级仅供内部参考；利息保障倍数暂不"
            "纳入输出，列入候选增强。"
        ),
        "elsewhere": (
            "现金流质量找「收益质量分析与应计项排雷、FCF」；"
            "或有负债找「关键审计事项与或有负债排查」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            rows("evidence_items", metric="text", value="number", compute_id="text"),
            OutputCell(name="current_ratio", type="number"),
            OutputCell(name="quick_ratio", type="number"),
            OutputCell(name="cash_to_current_debt", type="number"),
            OutputCell(name="altman_z_score", type="number"),
            OutputCell(name="solvency_risk_level", type="text"),
        ],
        "checks": [
            check("list_min", "evidence_items", min=4),
            check(
                "each_has",
                "evidence_items",
                keys=["metric", "value", "compute_id"],
                unique_by=["metric"],
                field="metric",
                values=[
                    "current_ratio",
                    "quick_ratio",
                    "cash_to_current_debt",
                    "altman_z_score",
                ],
            ),
            check("computed", "evidence_items", tolerance=0.0001),
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "current_ratio",
                        "quick_ratio",
                        "cash_to_current_debt",
                        "altman_z_score",
                        "solvency_risk_level",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "solvency_risk_level"},
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "current_ratio",
                    "expression": "current_assets / current_liabilities",
                    "tolerance": 0.01,
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "quick_ratio",
                    "expression": "(current_assets - inventory) / current_liabilities",
                    "tolerance": 0.01,
                },
            ),
        ],
    },
    # M06: 营运资本周转与现金转化周期 CCC
    {
        "id": "working_capital_cash_cycle_analysis",
        "title": "营运资本周转与现金转化周期 CCC",
        "category": "solvency",
        "tags": ["存货周转天数", "应收周转天数", "应付周转天数", "现金循环周期CCC"],
        "does": (
            "测算存货周转天数(DIO)、应收账款周转天数(DSO)、应付账款周转天数(DPO)，"
            "合成现金转化周期(CCC=DIO+DSO−DPO)；存货、应收、应付取期初期末算术平均，"
            "DIO=平均存货/营业成本×365，DSO=平均应收账款/营业收入×365，"
            "DPO=平均应付账款/营业成本×365，一律按365天。"
        ),
        "why": (
            "衡量企业经营营运资金占用效率，"
            "洞察产业链上下游话语权及日常流动性造血循环节奏。"
        ),
        "provenance": (
            "现金循环周期模型（Richards & Laughlin, 1980）及营运资本管理通行教材口径"
        ),
        "does_not": (
            "不接收入确认口径争议（以披露口径为准）；不分行业调整周转基准天数。"
        ),
        "elsewhere": (
            "营运假设驱动的预测找「三表联动预测模型」；"
            "偿债能力找「偿债压力与流动性体检」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="dio_days", type="number"),
            OutputCell(name="dso_days", type="number"),
            OutputCell(name="dpo_days", type="number"),
            OutputCell(name="cash_conversion_cycle_days", type="number"),
            OutputCell(name="working_capital_turnover", type="number"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "dio_days",
                        "dso_days",
                        "dpo_days",
                        "cash_conversion_cycle_days",
                        "working_capital_turnover",
                    ]
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "cash_conversion_cycle_days",
                    "expression": "dio_days + dso_days - dpo_days",
                    "tolerance": 0.1,
                },
            ),
        ],
    },
    # M07: 收益质量分析与应计项排雷、FCF
    {
        "id": "earnings_quality_accrual_audit",
        "title": "收益质量分析与应计项排雷、FCF",
        "category": "profitability",
        "tags": ["经营现金流净额比", "自由现金流FCF", "应计利润", "收益质量"],
        "does": (
            "测算经营性现金流/净利润比值、企业自由现金流(FCFF=OCF-CapEx)，"
            "并以总应计利润率((净利润−经营现金流)/总资产)的跨期异动标注应计排雷信号。"
        ),
        "why": (
            "穿透权责发生制下的会计应计项异动，识别纸面富贵与利润不含金现象，"
            "防范收益质量劣化。"
        ),
        "provenance": (
            "Dechow, Sloan & Sweeney（1995）修正琼斯模型（操纵性应计）与"
            "Dechow & Dichev（2002）应计质量框架；FCFF 口径见 Damodaran 估值教材"
        ),
        "does_not": (
            "完整修正琼斯模型需同行业面板回归，本轮以总应计利润率跨期异动替代，"
            "不输出行业调整后的操纵性应计估计；不接现金流预测。"
        ),
        "elsewhere": (
            "自由现金流预测与贴现找「企业自由现金流贴现估值 DCF」；"
            "报表真实性找「报表勾稽平衡与重述校验」。"
        ),
        "feeder_outputs": {
            "purified": [
                OutputCell(name="purified_net_profit", type="number"),
                OutputCell(name="recurring_profit_margin", type="number"),
                OutputCell(name="quarterly_unpacked_profits", type="list"),
            ]
        },
        "inputs": {"feeder_purified": "purified"},
        "expert_inputs": {"purified": "non_recurring_items_purification"},
        "outputs": [
            rows("evidence_items", metric="text", value="number", compute_id="text"),
            OutputCell(name="operating_cf_to_net_income", type="number"),
            OutputCell(name="free_cash_flow_firm", type="number"),
            OutputCell(name="accrual_anomaly_score", type="number"),
            OutputCell(name="earnings_quality_flag", type="text"),
        ],
        "checks": [
            check("list_min", "evidence_items", min=3),
            check(
                "each_has",
                "evidence_items",
                keys=["metric", "value", "compute_id"],
                unique_by=["metric"],
                field="metric",
                values=[
                    "operating_cf_to_net_income",
                    "free_cash_flow_firm",
                    "accrual_anomaly_score",
                ],
            ),
            check("computed", "evidence_items", tolerance=0.0001),
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "operating_cf_to_net_income",
                        "free_cash_flow_firm",
                        "accrual_anomaly_score",
                        "earnings_quality_flag",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "earnings_quality_flag"},
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "free_cash_flow_firm",
                    "expression": "operating_cash_flow - capex",
                    "tolerance": 0.01,
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "operating_cf_to_net_income",
                    "expression": "operating_cash_flow / net_income",
                    "tolerance": 0.0001,
                },
            ),
        ],
    },
    # M08: 主营业务分部量价拆解
    {
        "id": "segment_volume_price_decomposition",
        "title": "主营业务分部量价拆解",
        "category": "operations",
        "tags": ["分部报告", "销量拆解", "单价拆解", "收入驱动"],
        "does": (
            "按产品大类、业务分部及地理区域拆解收入构成，"
            "剥离销量增长率与平均单价变动效应：量增贡献=(本期量−上期量)×上期价，"
            "价变贡献=(本期价−上期价)×本期量，交叉项归入价变；"
            "分部未披露量价时用收入增速与销量增速近似并明确标注。"
        ),
        "why": (
            "穿透收入表象，判定业务扩张究竟由终端放量还是提价驱动，"
            "研判核心业务成长质量。"
        ),
        "provenance": (
            "财政部《企业会计准则第35号——分部报告》(CAS 35)；"
            "管理会计因素分析（连环替代）法惯例"
        ),
        "does_not": (
            "不接未披露任何分部数据的公司（输出缺口说明，不编造分部数）；"
            "不接分部的盈利能力归因。"
        ),
        "elsewhere": (
            "客户与供应商依赖度找「前五大客户与供应商集中度透视」；"
            "成长外推找「复合年均增长率与趋势外推」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="segment_revenue_shares", type="list"),
            OutputCell(name="volume_growth_rates", type="list"),
            OutputCell(name="price_variance_effects", type="list"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "segment_revenue_shares",
                        "volume_growth_rates",
                        "price_variance_effects",
                    ]
                },
            ),
            CheckCell(
                template="list_min",
                params={"path": "segment_revenue_shares", "min": 1},
            ),
            CheckCell(
                template="sourced",
                params={"path": "segment_revenue_shares", "tolerance": 0.01},
            ),
        ],
    },
    # M09: 前五大客户与供应商集中度透视
    {
        "id": "customer_supplier_concentration_scan",
        "title": "前五大客户与供应商集中度透视",
        "category": "operations",
        "tags": ["客户集中度", "供应商集中度", "供应链议价", "关联方"],
        "does": (
            "统计前五大客户销售占比、前五大供应商采购占比，"
            "排查关联方交易比重及重叠依存度。"
        ),
        "why": "评估供应链断裂风险与大客户依赖风险，洞察对上下游的实质议价地位。",
        "provenance": (
            "中国证监会及沪深交易所《股票上市规则》关于年报前五大客户与供应商披露规范"
        ),
        "does_not": (
            "客户/供应商以「客户一、客户二」脱敏披露时只做比例统计，不做身份穿透；"
            "关联方认定以 CAS 36 披露为准，不自行扩大。"
        ),
        "elsewhere": (
            "重大关联交易穿透找「股权质押、减持与重大关联交易穿透」；"
            "分部结构找「主营业务分部量价拆解」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="top5_customer_ratio", type="number"),
            OutputCell(name="top5_supplier_ratio", type="number"),
            OutputCell(name="concentration_risk_rating", type="text"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "top5_customer_ratio",
                        "top5_supplier_ratio",
                        "concentration_risk_rating",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "concentration_risk_rating"},
            ),
            CheckCell(
                template="sourced",
                params={"path": "top5_customer_ratio", "tolerance": 0.001},
            ),
        ],
    },
    # M10: 资本开支与产能释放周期追踪
    {
        "id": "capex_capacity_cycle_tracker",
        "title": "资本开支与产能释放周期追踪",
        "category": "assets",
        "tags": ["资本开支", "在建工程", "固定资产", "产能周期"],
        "does": (
            "分析购建固定资产支付现金(CapEx)与当期折旧摊销之比、在建工程结转固定资产"
            "进度（本期转固/期初在建）及折旧摊销年限变动，定位产能周期阶段。"
        ),
        "why": "锚定企业在产能扩张周期中所处阶段，研判新增折旧压力与未来业绩释放时点。",
        "provenance": (
            "财政部《企业会计准则第4号——固定资产》(CAS 4) 折旧估计规范及"
            "产能周期分析通行框架"
        ),
        "does_not": (
            "不评估项目投产后的实际达产率（无披露数据源）；"
            "产能爬坡只按在建工程与公告口径定性分级。"
        ),
        "elsewhere": (
            "资本开支对应的现金流压力找「收益质量分析与应计项排雷、FCF」；"
            "折旧政策变更找「报表勾稽平衡与重述校验」的重述线索。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="capex_to_depreciation", type="number"),
            OutputCell(name="cip_turnover_rate", type="number"),
            OutputCell(name="capacity_expansion_stage", type="text"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "capex_to_depreciation",
                        "cip_turnover_rate",
                        "capacity_expansion_stage",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "capacity_expansion_stage"},
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "capex_to_depreciation",
                    "expression": "capex / depreciation_amortization",
                    "tolerance": 0.01,
                },
            ),
        ],
    },
    # M11: 研发投入资本化与产出效能评估
    {
        "id": "rnd_capitalization_efficiency_eval",
        "title": "研发投入资本化与产出效能评估",
        "category": "assets",
        "tags": ["研发费用率", "资本化比例", "无形资产", "专利产出"],
        "does": (
            "核算研发投入强度(研发投入总额/营业收入，含费用化与资本化)、"
            "研发支出资本化率(资本化研发/研发投入总额)，"
            "对标专利获批量及新产品收入转化周期。"
        ),
        "why": "防范通过过度资本化虚增当期利润，衡量真实研发硬核科技转化效能。",
        "provenance": (
            "财政部《企业会计准则第6号——无形资产》(CAS 6) 研发支出资本化条件"
        ),
        "does_not": (
            "不接研发立项质量评估；专利数量与新产品收入以公司披露为准，"
            "不做外部专利库核验。"
        ),
        "elsewhere": (
            "研发资本化对利润的影响找「非经常性损益穿透与扣非还原」口径核对；"
            "资本化形成的无形资产减值找「商誉减值与资产质量压力测试」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="rnd_intensity", type="number"),
            OutputCell(name="capitalization_ratio", type="number"),
            OutputCell(name="rnd_revenue_conversion", type="number"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "rnd_intensity",
                        "capitalization_ratio",
                        "rnd_revenue_conversion",
                    ]
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "rnd_intensity",
                    "expression": "rnd_total_expenditure / revenue",
                    "tolerance": 0.0001,
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "capitalization_ratio",
                    "expression": "rnd_capitalized / rnd_total_expenditure",
                    "tolerance": 0.0001,
                },
            ),
        ],
    },
    # M12: 复合年均增长率与趋势外推
    {
        "id": "historical_cagr_trend_extrapolation",
        "title": "复合年均增长率与趋势外推",
        "category": "forecasting",
        "tags": ["CAGR", "营收年均复合", "净利复合", "趋势回归"],
        "does": (
            "计算3年/5年营业收入与扣非净利润的复合年均增长率(CAGR，几何平均)，"
            "拟合历史时序回归趋势；基期或终期为负、为零时CAGR无定义，"
            "输出「不可计算」并改报绝对变动额，不做符号技巧处理。"
        ),
        "why": "消除单一年份周期波动假象，确立企业中长期历史内生增长基准轨道。",
        "provenance": ("金融统计学几何平均复合年均增长率(CAGR)及对数线性时序回归模型"),
        "does_not": (
            "不接未来预测与区间预测（属「三表联动预测模型」）；"
            "趋势外推仅作历史参考线，不写成预期。"
        ),
        "elsewhere": (
            "未来两个季度的区间预测找「三表联动预测模型」；"
            "单季度还原找「非经常性损益穿透与扣非还原」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="revenue_cagr_3yr", type="number"),
            OutputCell(name="net_profit_cagr_3yr", type="number"),
            OutputCell(name="trend_projection_series", type="list"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "revenue_cagr_3yr",
                        "net_profit_cagr_3yr",
                        "trend_projection_series",
                    ]
                },
            ),
            CheckCell(
                template="list_min",
                params={"path": "trend_projection_series", "min": 1},
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "revenue_cagr_3yr",
                    "expression": "(end_revenue / start_revenue) ** (1 / 3) - 1",
                    "tolerance": 0.0001,
                },
            ),
        ],
    },
    # M13: 三表联动预测模型
    {
        "id": "three_statement_linked_forecasting",
        "title": "三表联动预测模型",
        "category": "forecasting",
        "tags": ["三表联动", "财务建模", "资产负债配平", "未来预测"],
        "does": (
            "基于历史基准、杜邦分解与营运资本假设联动推演三表，"
            "输出覆盖未来两个季度、按悲观/基准/乐观三情景的区间预测；"
            "资产负债表用现金/短期债务作平衡项(Plug)保持配平，配平残差恒零。"
        ),
        "why": "构建逻辑自洽的前瞻财务沙盘，为内生估值与资本配置提供基准预测三表。",
        "provenance": (
            "华尔街专业财务建模(Wall Street Prep)三表动态配平与循环引用解算框架；"
            "情景分析见 CFA 财务建模与预测框架"
        ),
        "does_not": (
            "不接五年以上长期预测；情景参数（收入增速区间、毛利波动）属作者假设，"
            "随委托单列示取值与依据，不写死；预测表行结构（季度×情景×科目）"
            "待真实数据接入后定形。"
        ),
        "elsewhere": (
            "历史增长基准找「复合年均增长率与趋势外推」；"
            "预测现金流的贴现估值找「企业自由现金流贴现估值 DCF」。"
        ),
        "feeder_outputs": {
            "dupont": [
                OutputCell(name="roe", type="number"),
                OutputCell(name="dupont_chain_product", type="number"),
                OutputCell(name="tax_burden", type="number"),
                OutputCell(name="interest_burden", type="number"),
                OutputCell(name="ebit_margin", type="number"),
                OutputCell(name="asset_turnover", type="number"),
                OutputCell(name="equity_multiplier", type="number"),
                rows(
                    "dupont_evidence_items",
                    metric="text",
                    value="number",
                    compute_id="text",
                ),
            ],
            "working_capital": [
                OutputCell(name="dio_days", type="number"),
                OutputCell(name="dso_days", type="number"),
                OutputCell(name="dpo_days", type="number"),
                OutputCell(name="cash_conversion_cycle_days", type="number"),
                OutputCell(name="working_capital_turnover", type="number"),
            ],
        },
        "inputs": {"feeder_dupont": "dupont", "feeder_wc": "working_capital"},
        "expert_inputs": {
            "dupont": "dupont_five_way_decomposition",
            "working_capital": "working_capital_cash_cycle_analysis",
        },
        "outputs": [
            rows(
                "forecast_income_statement",
                horizon="text",
                period="text",
                scenario="text",
                metric="text",
            ),
            rows(
                "forecast_balance_sheet",
                horizon="text",
                period="text",
                scenario="text",
                assets="number",
                liabilities="number",
                equity="number",
            ),
            rows(
                "forecast_cashflow_statement",
                horizon="text",
                period="text",
                scenario="text",
                opening_cash="number",
                closing_cash="number",
                cash_increase="number",
            ),
            rows(
                "forecast_intervals",
                horizon="text",
                metric="text",
                lower="number",
                base="number",
                upper="number",
            ),
            rows("forecast_assumptions", name="text", reason="text", source="text"),
        ],
        "checks": [
            required(
                "forecast_income_statement",
                "forecast_balance_sheet",
                "forecast_cashflow_statement",
                "forecast_intervals",
                "forecast_assumptions",
            ),
            *forecast_checks(
                "forecast_income_statement",
                ["revenue", "parent_net_profit", "operating_cost"],
            ),
            *forecast_balance_checks(
                "forecast_balance_sheet",
                ["assets", "liabilities", "equity"],
                "abs(assets - liabilities - equity)",
            ),
            *forecast_balance_checks(
                "forecast_cashflow_statement",
                ["opening_cash", "closing_cash", "cash_increase"],
                "abs(closing_cash - opening_cash - cash_increase)",
            ),
            check("list_min", "forecast_intervals", min=6),
            check(
                "each_has",
                "forecast_intervals",
                keys=["horizon", "metric", "lower", "base", "upper"],
                unique_by=["horizon", "metric"],
                field="horizon",
                values=["next_quarter", "following_quarter"],
            ),
            check(
                "each_has",
                "forecast_intervals",
                field="metric",
                values=["revenue", "parent_net_profit", "gross_margin"],
            ),
            check(
                "computed",
                "forecast_intervals",
                expression="max(lower - base, base - upper, 0)",
                input_keys=["lower", "base", "upper"],
                expected_value=0,
                tolerance=0,
            ),
            check("list_min", "forecast_assumptions", min=1),
            check(
                "each_has",
                "forecast_assumptions",
                keys=["name", "reason", "source"],
                non_empty_keys=["name", "reason", "source"],
                unique_by=["name"],
            ),
        ],
    },
    # M14: 企业自由现金流贴现估值 DCF
    {
        "id": "dcf_intrinsic_valuation",
        "title": "企业自由现金流贴现估值 DCF",
        "category": "valuation",
        "tags": ["DCF", "WACC", "终值TV", "企业价值", "内在价值"],
        "does": (
            "以三表联动预测的FCFF为显性期现金流，按加权平均资本成本(WACC)贴现，"
            "加永续增长终值推导企业价值与每股内在价值；WACC的无风险利率、ERP、beta"
            "逐项标注来源，显性期长度与永续增长率随委托单列示取值与依据；"
            "输出WACC×永续增长率双变量敏感性表。"
        ),
        "why": (
            "脱离短期二级市场噪音，立足企业终身自由现金流创造能力核算真实内在底线价值。"
        ),
        "provenance": (
            "纽约大学斯特恩商学院阿斯瓦斯·达摩达兰(Aswath Damodaran)贴现现金流估值专著"
        ),
        "does_not": (
            "不接目标价与买卖建议表述（平台红线）；"
            "银行、保险不适用FCFF贴现（应改用DDM或超额收益法，另行立项）。"
        ),
        "elsewhere": (
            "显性期现金流来源找「三表联动预测模型」；"
            "市场乘数交叉验证找「相对估值与历史分位数对标」。"
        ),
        "feeder_outputs": {
            "forecast": [
                OutputCell(name="forecast_income_statement", type="table"),
                OutputCell(name="forecast_balance_sheet", type="table"),
                OutputCell(name="forecast_cashflow_statement", type="table"),
                OutputCell(name="balance_sheet_plug_zero", type="number"),
            ]
        },
        "inputs": {"feeder_forecast": "forecast"},
        "expert_inputs": {"forecast": "three_statement_linked_forecasting"},
        "outputs": [
            rows("evidence_items", metric="text", value="number", compute_id="text"),
            OutputCell(name="enterprise_value_dcf", type="number"),
            OutputCell(name="equity_value_dcf", type="number"),
            OutputCell(name="per_share_intrinsic_value", type="number"),
            OutputCell(name="wacc_used", type="number"),
            OutputCell(name="sensitivity_table", type="table"),
        ],
        "checks": [
            check("list_min", "evidence_items", min=4),
            check(
                "each_has",
                "evidence_items",
                keys=["metric", "value", "compute_id"],
                unique_by=["metric"],
                field="metric",
                values=[
                    "enterprise_value_dcf",
                    "equity_value_dcf",
                    "per_share_intrinsic_value",
                    "wacc_used",
                ],
            ),
            check("computed", "evidence_items", tolerance=0.0001),
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "enterprise_value_dcf",
                        "equity_value_dcf",
                        "per_share_intrinsic_value",
                        "wacc_used",
                        "sensitivity_table",
                    ]
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "equity_value_dcf",
                    "expression": "enterprise_value_dcf - net_debt",
                    "tolerance": 0.01,
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "per_share_intrinsic_value",
                    "expression": "equity_value_dcf / shares_outstanding",
                    "tolerance": 0.01,
                },
            ),
        ],
    },
    # M15: 相对估值与历史分位数对标
    {
        "id": "relative_multiple_valuation",
        "title": "相对估值与历史分位数对标",
        "category": "valuation",
        "tags": ["PE-TTM", "PB-LF", "PS-TTM", "历史分位数", "可比公司"],
        "does": (
            "计算PE-TTM、PB-LF、EV/EBITDA乘数（EV=市值+净债务），"
            "按日频分布测算所处历史3年/5年分位数，对标行业可比公司中位数。"
        ),
        "why": "衡量标的在横向行业及纵向历史估值谱系中的溢折价状态与安全边际。",
        "provenance": (
            "Damodaran 相对估值与乘数对标框架；PE-TTM/PB-LF 为中国市场终端"
            "（Wind、同花顺）通行口径"
        ),
        "does_not": (
            "不接亏损公司PE（无意义，转PB/PS并标注）；"
            "可比公司名单由作者确认，不自动圈选。"
        ),
        "elsewhere": (
            "内在价值锚找「企业自由现金流贴现估值 DCF」；"
            "盈利质量前提找「收益质量分析与应计项排雷、FCF」。"
        ),
        "feeder_outputs": {
            "purified": [
                OutputCell(name="purified_net_profit", type="number"),
                OutputCell(name="recurring_profit_margin", type="number"),
                OutputCell(name="quarterly_unpacked_profits", type="list"),
            ]
        },
        "inputs": {"feeder_purified": "purified"},
        "expert_inputs": {"purified": "non_recurring_items_purification"},
        "outputs": [
            OutputCell(name="target_pe_ttm", type="number"),
            OutputCell(name="target_pb_lf", type="number"),
            OutputCell(name="historical_pe_percentile", type="number"),
            OutputCell(name="implied_market_cap", type="number"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "target_pe_ttm",
                        "target_pb_lf",
                        "historical_pe_percentile",
                        "implied_market_cap",
                    ]
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "implied_market_cap",
                    "expression": "purified_net_profit * target_pe_ttm",
                    "tolerance": 0.01,
                },
            ),
        ],
    },
    # M16: 股权质押、减持与重大关联交易穿透
    {
        "id": "pledge_reduction_related_transaction_scan",
        "title": "股权质押、减持与重大关联交易穿透",
        "category": "governance",
        "tags": ["股权质押", "控股股东", "大股东减持", "关联交易", "治理风险"],
        "does": (
            "监控控股股东与实际控制人累计质押比例与平仓线预警，"
            "扫描高管大股东减持计划及重大关联方资金往来。"
        ),
        "why": (
            "防范大股东质押爆仓导致的控制权变更风险，识别掏空上市公司与利益输送隐患。"
        ),
        "provenance": (
            "中国证监会《上市公司股东、董监高减持股份的若干规定》"
            "及《企业会计准则第36号——关联方披露》"
        ),
        "does_not": (
            "数据以公告披露为准，不接实时盘口监控；平仓线测算的质押率假设按券商"
            "通行50%–60%区间标注，不输出单点预警线。"
        ),
        "elsewhere": (
            "公司治理与护城河研判找「竞争壁垒与核心投资假说验证」；"
            "或有负债找「关键审计事项与或有负债排查」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="controlling_shareholder_pledge_ratio", type="number"),
            OutputCell(name="major_holder_reduction_plans", type="list"),
            OutputCell(name="related_party_anomaly_score", type="number"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "controlling_shareholder_pledge_ratio",
                        "major_holder_reduction_plans",
                        "related_party_anomaly_score",
                    ]
                },
            ),
            CheckCell(
                template="sourced",
                params={
                    "path": "controlling_shareholder_pledge_ratio",
                    "tolerance": 0.001,
                },
            ),
        ],
    },
    # M17: 商誉减值与资产质量压力测试
    {
        "id": "goodwill_asset_impairment_stress_test",
        "title": "商誉减值与资产质量压力测试",
        "category": "governance",
        "tags": ["商誉", "资产减值", "净资产占比", "业绩承诺", "压力测试"],
        "does": (
            "测算商誉占净资产比重、并购标的业绩承诺完成率(实际/承诺)，"
            "并测算商誉全额减值压力情景对当期损益与净资产的侵蚀幅度。"
        ),
        "why": (
            "提前引爆商誉减值黑天鹅，评估资产负债表潜在存货跌价、坏账及商誉暴雷风险。"
        ),
        "provenance": (
            "财政部《企业会计准则第8号——资产减值》(CAS 8) "
            "及会计监管风险提示第8号——商誉减值"
        ),
        "does_not": (
            "可收回金额的公允价值测算属评估师工作，本轮只做账面与承诺完成率的"
            "压力测试，不输出正式减值结论。"
        ),
        "elsewhere": (
            "无形资产与研发资本化质量找「研发投入资本化与产出效能评估」；"
            "减值对偿债指标的冲击找「偿债压力与流动性体检」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            OutputCell(name="goodwill_to_net_assets", type="number"),
            OutputCell(name="cgu_recoverable_amount", type="number"),
            OutputCell(name="impairment_stress_headroom", type="number"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "goodwill_to_net_assets",
                        "cgu_recoverable_amount",
                        "impairment_stress_headroom",
                    ]
                },
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "goodwill_to_net_assets",
                    "expression": "goodwill_balance / net_assets",
                    "tolerance": 0.0001,
                },
            ),
            CheckCell(
                template="identity",
                params={"path": "impairment_stress_headroom", "tolerance": 0.01},
            ),
        ],
    },
    # M18: 关键审计事项与或有负债排查
    {
        "id": "kam_contingent_liability_audit",
        "title": "关键审计事项与或有负债排查",
        "category": "audit",
        "tags": ["KAM", "审计意见", "对外担保", "未决诉讼", "或有负债"],
        "does": (
            "提取审计意见类型与关键审计事项（KAM），每条结论必须带原文引文及"
            "PDF物理页码（与印刷页码不一致时注明）；扫描附注中的对外担保、"
            "未决诉讼及承诺事项，或有负债按CAS 13口径归类。"
        ),
        "why": (
            "倾听外部独立审计师的警示哨声，排查表外隐性负债对净资产的刚性求偿风险。"
        ),
        "provenance": (
            "中国注册会计师审计准则第1504号《在审计报告中沟通关键审计事项》"
            "及《企业会计准则第13号——或有事项》"
        ),
        "does_not": (
            "不接审计意见真实性的再鉴定；未取得带页码原文时标「待核」，"
            "不以转述替代引文。"
        ),
        "elsewhere": (
            "报表数字真实性找「报表勾稽平衡与重述校验」；"
            "关联交易穿透找「股权质押、减持与重大关联交易穿透」。"
        ),
        "feeder_outputs": {
            "reconciled": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ]
        },
        "inputs": {"feeder_reconciled": "reconciled"},
        "expert_inputs": {"reconciled": "accounting_statement_reconciliation"},
        "outputs": [
            rows("audit_opinion_type", kind="text", text="text"),
            OutputCell(name="kam_topics_count", type="number"),
            rows("kam_matters", kind="text", matter="text", audit_response="text"),
            OutputCell(name="external_guarantee_ratio", type="number"),
            OutputCell(name="contingent_liability_risk_verdict", type="text"),
        ],
        "checks": [
            required(
                "audit_opinion_type",
                "kam_topics_count",
                "kam_matters",
                "external_guarantee_ratio",
                "contingent_liability_risk_verdict",
            ),
            check("list_min", "audit_opinion_type", min=1),
            check(
                "each_has",
                "audit_opinion_type",
                keys=["kind", "text"],
                non_empty_keys=["text"],
                field="kind",
                values=["opinion"],
            ),
            check("quoted", "audit_opinion_type", field="kind", equals="opinion"),
            check("list_min", "kam_matters", min=1),
            check(
                "each_has",
                "kam_matters",
                keys=["kind", "matter", "audit_response"],
                non_empty_keys=["matter", "audit_response"],
                unique_by=["matter"],
                field="kind",
                values=["kam"],
            ),
            check("quoted", "kam_matters", field="kind", equals="kam"),
        ],
    },
    # M19: 竞争壁垒与核心投资假说验证
    {
        "id": "competitive_moat_thesis_synthesis",
        "title": "竞争壁垒与核心投资假说验证",
        "category": "moat",
        "tags": ["经济护城河", "ROIC", "超额收益", "投资假说", "壁垒验证"],
        "does": (
            "综合ROIC与WACC利差（持续性为核心证据），结合无形资产、转换成本、"
            "网络效应、成本优势四类来源逐项举证，对照核心买入假说进行事实证伪；"
            "护城河分级判据：利差连续3年为正且至少一类来源有实证为「强」，"
            "利差为正但证据不足为「中」，利差为负或转负为「弱」。"
        ),
        "why": (
            "将财务指标升华为商业壁垒研判，"
            "验证超额资本回报率的可持续性与竞争护城河深浅。"
        ),
        "provenance": (
            "晨星(Morningstar)经济护城河评级标准及格林沃尔德(Greenwald)竞争优势理论"
        ),
        "does_not": ("不接品牌价值评估；分级为内部分析标签，不对标晨星对外评级结果。"),
        "elsewhere": (
            "ROIC 与 WACC 的取值口径找「企业自由现金流贴现估值 DCF」；"
            "假说涉及的财务证据回到各上游分析方法。"
        ),
        "feeder_outputs": {
            "dupont": [
                OutputCell(name="roe", type="number"),
                OutputCell(name="dupont_chain_product", type="number"),
                OutputCell(name="tax_burden", type="number"),
                OutputCell(name="interest_burden", type="number"),
                OutputCell(name="ebit_margin", type="number"),
                OutputCell(name="asset_turnover", type="number"),
                OutputCell(name="equity_multiplier", type="number"),
                rows(
                    "dupont_evidence_items",
                    metric="text",
                    value="number",
                    compute_id="text",
                ),
            ]
        },
        "inputs": {"feeder_dupont": "dupont"},
        "expert_inputs": {"dupont": "dupont_five_way_decomposition"},
        "outputs": [
            OutputCell(name="moat_rating", type="text"),
            OutputCell(name="roic_vs_wacc_spread", type="number"),
            OutputCell(name="thesis_verification_matrix", type="table"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "moat_rating",
                        "roic_vs_wacc_spread",
                        "thesis_verification_matrix",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "moat_rating"},
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "roic_vs_wacc_spread",
                    "expression": "roic - wacc",
                    "tolerance": 0.0001,
                },
            ),
        ],
    },
    # M20: 结构化研究底稿与报告成稿编排 (强制消费真实分析成果 DAG 与 covers 规则)
    {
        "id": "comprehensive_dossier_formatting",
        "title": "结构化研究底稿与报告成稿编排",
        "category": "reporting",
        "tags": ["成稿编排", "研究底稿", "底线排雷", "估值底稿", "归档"],
        "does": (
            "聚合勾稽校验、杜邦分解、偿债流动性、收益质量与DCF估值五个上游单元的"
            "结构化产物，编排研究底稿：进入底稿的每项指标必须在证据审计索引中带上游"
            "证据编号，索引与上游证据清单一一对应（不多、不少、不重）；"
            "风险因素至少3条、每条带具体内容与来源标签；估值以区间加关键假设并列呈现，"
            "不给单点目标价；同时输出执行摘要与归档索引。"
        ),
        "why": (
            "作为全流程最终交付单元，消费并核验全部已分析指标，"
            "杜绝跳过核心分析两步直通。"
        ),
        "provenance": (
            "中国证券业协会《发布证券研究报告执业规范》（2020年修订）研报底稿与"
            "留痕要求；《证券分析师执业行为准则》信息来源披露义务"
        ),
        "does_not": (
            "不接买卖建议、评级与目标价表述（平台红线）；不接未在上游出现的指标，"
            "底稿不凭空造数；估值区间与关键假设的对外表述待作者确认。"
        ),
        "elsewhere": (
            "单项指标计算与校验回到各上游方法；缺数据的需求提报走 knowfeed 缺口清单。"
        ),
        "feeder_outputs": {
            "reconciliation": [
                OutputCell(name="reconciliation_status", type="text"),
                OutputCell(name="balance_sheet_balance_diff", type="number"),
                OutputCell(name="audit_reconciliations", type="table"),
            ],
            "dupont": [
                OutputCell(name="roe", type="number"),
                OutputCell(name="dupont_chain_product", type="number"),
                OutputCell(name="tax_burden", type="number"),
                OutputCell(name="interest_burden", type="number"),
                OutputCell(name="ebit_margin", type="number"),
                OutputCell(name="asset_turnover", type="number"),
                OutputCell(name="equity_multiplier", type="number"),
                rows(
                    "dupont_evidence_items",
                    metric="text",
                    value="number",
                    compute_id="text",
                ),
            ],
            "solvency": [
                OutputCell(name="current_ratio", type="number"),
                OutputCell(name="quick_ratio", type="number"),
                OutputCell(name="cash_to_current_debt", type="number"),
                OutputCell(name="altman_z_score", type="number"),
                OutputCell(name="solvency_risk_level", type="text"),
            ],
            "earnings_quality": [
                OutputCell(name="operating_cf_to_net_income", type="number"),
                OutputCell(name="free_cash_flow_firm", type="number"),
                OutputCell(name="accrual_anomaly_score", type="number"),
                OutputCell(name="earnings_quality_flag", type="text"),
            ],
            "valuation": [
                OutputCell(name="enterprise_value_dcf", type="number"),
                OutputCell(name="equity_value_dcf", type="number"),
                OutputCell(name="per_share_intrinsic_value", type="number"),
                OutputCell(name="wacc_used", type="number"),
                OutputCell(name="sensitivity_table", type="table"),
            ],
        },
        "inputs": {
            "feeder_rec": "reconciliation",
            "feeder_dupont": "dupont",
            "feeder_solv": "solvency",
            "feeder_eq": "earnings_quality",
            "feeder_val": "valuation",
        },
        "expert_inputs": {
            "reconciliation": "accounting_statement_reconciliation",
            "dupont": "dupont_five_way_decomposition",
            "solvency": "solvency_liquidity_stress_check",
            "earnings_quality": "earnings_quality_accrual_audit",
            "valuation": "dcf_intrinsic_valuation",
        },
        "outputs": [
            rows("reconciliation_evidence", metric="text", source="text"),
            rows("solvency_evidence", metric="text", source="text"),
            rows("earnings_evidence", metric="text", source="text"),
            rows("valuation_evidence", metric="text", source="text"),
            OutputCell(name="structured_dossier", type="text"),
            OutputCell(name="executive_summary", type="text"),
            OutputCell(
                name="risk_factors",
                type="list",
                items=(
                    OutputField(name="text", type="text"),
                    OutputField(name="source", type="text"),
                ),
            ),
            rows("evidence_audit_trail", metric="text", source="text"),
        ],
        "checks": [
            check("list_min", "evidence_audit_trail", min=1),
            check(
                "each_has",
                "evidence_audit_trail",
                keys=["metric", "source"],
                non_empty_keys=["metric", "source"],
                unique_by=["metric"],
                field="source",
                values=["dupont_five_way_decomposition"],
            ),
            check(
                "covers",
                "reconciliation_evidence",
                field="metric",
                source_step="feeder_rec",
                source_path="audit_reconciliations",
                source_field="check_name",
            ),
            check("list_min", "reconciliation_evidence", min=1),
            check(
                "each_has",
                "reconciliation_evidence",
                keys=["metric", "source"],
                non_empty_keys=["metric", "source"],
                unique_by=["metric"],
                field="source",
                values=["accounting_statement_reconciliation"],
            ),
            check(
                "covers",
                "solvency_evidence",
                field="metric",
                source_step="feeder_solv",
                source_path="evidence_items",
                source_field="metric",
            ),
            check("list_min", "solvency_evidence", min=1),
            check(
                "each_has",
                "solvency_evidence",
                keys=["metric", "source"],
                non_empty_keys=["metric", "source"],
                unique_by=["metric"],
                field="source",
                values=["solvency_liquidity_stress_check"],
            ),
            check(
                "covers",
                "earnings_evidence",
                field="metric",
                source_step="feeder_eq",
                source_path="evidence_items",
                source_field="metric",
            ),
            check("list_min", "earnings_evidence", min=1),
            check(
                "each_has",
                "earnings_evidence",
                keys=["metric", "source"],
                non_empty_keys=["metric", "source"],
                unique_by=["metric"],
                field="source",
                values=["earnings_quality_accrual_audit"],
            ),
            check(
                "covers",
                "valuation_evidence",
                field="metric",
                source_step="feeder_val",
                source_path="evidence_items",
                source_field="metric",
            ),
            check("list_min", "valuation_evidence", min=1),
            check(
                "each_has",
                "valuation_evidence",
                keys=["metric", "source"],
                non_empty_keys=["metric", "source"],
                unique_by=["metric"],
                field="source",
                values=["dcf_intrinsic_valuation"],
            ),
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "structured_dossier",
                        "executive_summary",
                        "risk_factors",
                        "evidence_audit_trail",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "structured_dossier"},
            ),
            CheckCell(
                template="non_empty",
                params={"path": "executive_summary"},
            ),
            CheckCell(
                template="list_min",
                params={"path": "risk_factors", "min": 3},
            ),
            CheckCell(
                template="each_has",
                params={
                    "path": "risk_factors",
                    "keys": ["text", "source"],
                    "non_empty_keys": ["text", "source"],
                    "unique_by": ["text"],
                    "field": "source",
                    "values": [
                        "accounting_statement_reconciliation",
                        "dupont_five_way_decomposition",
                        "solvency_liquidity_stress_check",
                        "earnings_quality_accrual_audit",
                        "dcf_intrinsic_valuation",
                    ],
                },
            ),
            CheckCell(
                template="covers",
                params={
                    "path": "evidence_audit_trail",
                    "field": "metric",
                    "source_step": "feeder_dupont",
                    "source_path": "dupont_evidence_items",
                    "source_field": "metric",
                },
            ),
        ],
    },
]

BATCH1_SPECS = ALL_20_SPECS[:8]
BATCH2_SPECS = ALL_20_SPECS[8:14]
BATCH3_SPECS = ALL_20_SPECS[14:]


# ---------------------------------------------------------------------------
# 单个方法主档冻结函数
# ---------------------------------------------------------------------------
def make_author_method_definition(
    spec: dict[str, Any],
    upstream_schemas: dict[str, Any] | None = None,
) -> MethodDefinition:
    """Creates an author-owned frozen MethodDefinition adhering to genuine DAG."""
    feeder_steps = []
    for feeder_id, slot_name in spec["inputs"].items():
        upstream_id = spec["expert_inputs"][slot_name]
        upstream_spec = next((s for s in ALL_20_SPECS if s["id"] == upstream_id), None)
        slot_outputs = (
            upstream_spec["outputs"]
            if upstream_spec
            else spec["feeder_outputs"][slot_name]
        )
        schema = upstream_schemas.get(upstream_id) if upstream_schemas else None
        if schema is None and upstream_spec:
            schema = _input_schema(
                StepCell(
                    id=upstream_id,
                    outputs=tuple(slot_outputs),
                    checks=tuple(upstream_spec["checks"]),
                )
            )
        feeder_steps.append(
            {
                "id": feeder_id,
                "title": f"前置数据供给({slot_name})",
                "does": f"供给{slot_name}所需的标准化字段结构。",
                "selection": {
                    "applies_when": "初始加载",
                    "required": True,
                    "max_calls": 1,
                },
                "outputs": [out.model_dump(mode="json") for out in slot_outputs],
                "checks": [
                    {
                        "template": "required",
                        "params": {"keys": [o.name for o in slot_outputs]},
                    }
                ],
                "advanced": {"output_schema": schema} if schema is not None else {},
            }
        )

    target_step = {
        "id": spec["id"],
        "title": spec["title"],
        "does": spec["does"],
        "why": spec["why"],
        "uses": list(spec["inputs"].keys()),
        "rules": [
            {"id": "r1", "text": f"依据：{spec['provenance']}"},
            {"id": "r2", "text": "严禁伪造数据，数值与引文必须精准对齐对应输入源。"},
            {"id": "scope_exclusions", "text": spec.get("does_not", "")},
            {"id": "scope_routing", "text": spec.get("elsewhere", "")},
        ],
        "outputs": [out.model_dump(mode="json") for out in spec["outputs"]],
        "checks": [check.model_dump(mode="json") for check in spec["checks"]],
        "selection": {
            "applies_when": (
                f"问题需要{spec['title']}。适用边界：{spec.get('does_not', '')}"
            ),
            "required": False,
            "max_calls": 1,
        },
    }

    workdraft = {
        "name": f"方法_{spec['title']}",
        "tagline": "",
        "scope": {
            "does": spec["does"],
            "does_not": spec.get("does_not", ""),
            "elsewhere": spec.get("elsewhere", ""),
        },
        "data_picks": [],
        "steps": [*feeder_steps, target_step],
        "planning": {
            "method_text": f"按需执行{spec['title']}，严格遵循准则与出处要求。",
            "final_units": [spec["id"]],
            "max_steps": len(feeder_steps) + 2,
            "max_replans": 1,
            "reserve": "0.05",
            "input_max_chars": 8000,
        },
    }
    body = WorkdraftBody.model_validate(workdraft)
    cmd = FreezeMethod(
        request_id=UUID(int=1),
        revision=1,
        step_id=spec["id"],
        input_slots=spec["inputs"],
    )
    return freeze(body, cmd)


# ---------------------------------------------------------------------------
# 专家总装快照生成
# ---------------------------------------------------------------------------
def build_financial_expert_manifest(
    specs: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    """Assembles a full financial expert workdraft binding methods via genuine DAG."""
    target_specs = specs if specs is not None else ALL_20_SPECS

    intake_step = StepCell(
        id="financial_data_intake",
        title="财务基础数据接入",
        does="读取公司年报与季报原始三表、附注披露及行情数据。",
        selection=MethodSelection(applies_when="初始接入", required=True, max_calls=1),
        outputs=(
            OutputCell(name="raw_balance_sheet", type="table"),
            OutputCell(name="raw_income_statement", type="table"),
            OutputCell(name="raw_cash_flow_statement", type="table"),
        ),
        checks=(
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "raw_balance_sheet",
                        "raw_income_statement",
                        "raw_cash_flow_statement",
                    ]
                },
            ),
        ),
    )

    upstream_schemas = {"financial_data_intake": _input_schema(intake_step)}
    bound_expert_steps = [intake_step]
    definitions = {}
    method_refs = {}
    method_sources = {}

    for idx, spec in enumerate(target_specs):
        m_id = spec["id"]
        defn = make_author_method_definition(spec, upstream_schemas)
        definitions[m_id] = defn
        digest = defn.digest()

        current_expert_draft = WorkdraftBody.model_validate(
            {
                "name": "全面财务与估值分析专家",
                "tagline": (
                    "基于不可变作者方法库构建的结构化财务三表重构、"
                    "估值推演、公司治理与护城河体检专家"
                ),
                "scope": {
                    "does": (
                        "对标的进行标准化三表重构、勾稽校验、杜邦分解、"
                        "流动性体检、营运周期、收益质量、DCF估值、相对估值、"
                        "三表预测、产能追踪、研发评估、商誉减值与治理穿透综合研判"
                    ),
                    "does_not": "不代客理财，不提供内幕交易信息",
                    "elsewhere": "",
                },
                "data_picks": [],
                "steps": [s.model_dump(mode="json") for s in bound_expert_steps],
                "planning": {
                    "method_text": (
                        "接收到公司代码与财报数据时，由数据接入步骤供给数据，"
                        "依次编排三表重构、勾稽平表、杜邦五步、三表预测、"
                        "DCF估值及综合底稿成稿。"
                    ),
                    "final_units": ["comprehensive_dossier_formatting"],
                    "max_steps": 25,
                    "max_replans": 2,
                    "reserve": "0.15",
                    "input_max_chars": 64000,
                },
            }
        )
        bind_cmd = BindMethod(
            request_id=UUID(int=5000 + idx),
            revision=1,
            alias=m_id,
            digest=digest,
            inputs=spec["expert_inputs"],
            required=False,
            max_calls=1,
        )
        bound_step = bind(defn, current_expert_draft, bind_cmd)
        bound_expert_steps.append(bound_step)
        upstream_schemas[m_id] = _input_schema(bound_step)

        ref = {
            "method_id": str(UUID(int=1000 + idx)),
            "version": 1,
            "digest": digest,
            "inputs": spec["expert_inputs"],
        }
        method_refs[m_id] = ref
        method_sources[m_id] = {
            "reference": ref,
            "definition": defn.document(),
        }

    final_draft = WorkdraftBody.model_validate(
        {
            "name": "全面财务与估值分析专家",
            "tagline": (
                "基于不可变作者方法库构建的结构化财务三表重构、"
                "估值推演、公司治理与护城河体检专家"
            ),
            "scope": {
                "does": (
                    "对标的进行标准化三表重构、勾稽校验、杜邦分解、"
                    "流动性体检、营运周期、收益质量、DCF估值、相对估值、"
                    "三表预测、产能追踪、研发评估、商誉减值与治理穿透综合研判"
                ),
                "does_not": "不代客理财，不提供内幕交易信息",
                "elsewhere": "",
            },
            "data_picks": [],
            "steps": [s.model_dump(mode="json") for s in bound_expert_steps],
            "method_refs": method_refs,
            "planning": {
                "method_text": (
                    "接收到公司代码与财报数据时，由数据接入步骤供给数据，"
                    "依次编排三表重构、勾稽平表、杜邦五步、三表预测、"
                    "DCF估值及综合底稿成稿。"
                ),
                "final_units": ["comprehensive_dossier_formatting"],
                "max_steps": 25,
                "max_replans": 2,
                "reserve": "0.15",
                "input_max_chars": 64000,
            },
        }
    )
    sources = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(final_draft))
    }
    return compile_manifest(
        final_draft,
        sources,
        pack_id="comprehensive-financial-expert-v1",
        version="1",
        runtime="knowbench",
        fallback_name="comprehensive-financial-expert",
        method_sources=method_sources,
    )


def export_financial_expert_manifest(target_path: Path | None = None) -> Path:
    """Explicit generator to export golden manifest fixture (not run in tests)."""
    import json

    path = (
        target_path or Path(__file__).parent / "fixtures/financial-expert-manifest.json"
    )
    manifest, problems = build_financial_expert_manifest()
    if problems:
        raise RuntimeError(f"Compiler problems: {problems}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return path
