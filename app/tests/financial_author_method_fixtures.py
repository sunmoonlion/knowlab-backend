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
from typing import Any
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
    StepCell,
    WorkdraftBody,
    cells,
)

SYNTHETIC_DATA_FLAG = True

# ---------------------------------------------------------------------------
# 20 个专家方法规范（包含真实业务 DAG 依赖与严格证据验收规则）
# ---------------------------------------------------------------------------
ALL_20_SPECS: list[dict[str, Any]] = [
    # M01: 财务三表标准化重构与口径对齐
    {
        "id": "fin_statement_restructure",
        "title": "财务三表标准化重构与口径对齐",
        "category": "accounting",
        "tags": ["三表标准化", "口径对齐", "CAS2024"],
        "does": (
            "读取原始披露三表，依CAS2024准则重构列报科目，"
            "统一货币计量单位并记录重分类调整溯源。"
        ),
        "why": "消除非标科目与列报口径差异，形成横向与跨期可比的基础分析底稿。",
        "provenance": (
            "财政部《企业会计准则第30号——财务报表列报》(CAS 30) 及监管报表披露规范"
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
            OutputCell(name="standardized_balance_sheet", type="table"),
            OutputCell(name="standardized_income_statement", type="table"),
            OutputCell(name="standardized_cash_flow", type="table"),
            OutputCell(name="reclassification_audit_trail", type="list"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "standardized_balance_sheet",
                        "standardized_income_statement",
                        "standardized_cash_flow",
                        "reclassification_audit_trail",
                    ]
                },
            ),
            CheckCell(
                template="list_min",
                params={"path": "standardized_balance_sheet", "min": 3},
            ),
            CheckCell(
                template="list_min",
                params={"path": "standardized_income_statement", "min": 3},
            ),
            CheckCell(
                template="list_min",
                params={"path": "standardized_cash_flow", "min": 3},
            ),
            CheckCell(
                template="list_min",
                params={"path": "reclassification_audit_trail", "min": 1},
            ),
            CheckCell(
                template="each_has",
                params={
                    "path": "standardized_balance_sheet",
                    "keys": ["item", "amount"],
                },
            ),
            CheckCell(
                template="sourced",
                params={"path": "standardized_balance_sheet", "tolerance": 0.01},
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
            "执行资产=负债+所有者权益等式校验(误差绝对值<1.00元)，"
            "校验净利润与未分配利润勾稽及前期追溯调整。"
        ),
        "why": (
            "验证三表横向纵向强勾稽平衡，排查会计不平衡或重述差错，建立分析可信基石。"
        ),
        "provenance": "中国注册会计师审计准则第1501号及三表勾稽平衡公理",
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
            OutputCell(name="audit_reconciliations", type="table"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "reconciliation_status",
                        "balance_sheet_balance_diff",
                        "audit_reconciliations",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "reconciliation_status"},
            ),
            CheckCell(
                template="list_min",
                params={"path": "audit_reconciliations", "min": 1},
            ),
            CheckCell(
                template="each_has",
                params={
                    "path": "audit_reconciliations",
                    "keys": ["check_name", "passed", "diff"],
                },
            ),
            CheckCell(
                template="all_equal",
                params={
                    "path": "audit_reconciliations",
                    "field": "passed",
                    "equals": True,
                },
            ),
            CheckCell(
                template="identity",
                params={"path": "balance_sheet_balance_diff", "tolerance": 1.0},
            ),
            CheckCell(
                template="computed",
                params={
                    "path": "balance_sheet_balance_diff",
                    "expression": "abs(total_assets - (total_liabilities + equity))",
                    "tolerance": 1.0,
                },
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
            "穿透政府补助、投资收益、资产处置等非经常性项目，"
            "还原单季度真实主营核心利润及持续经营盈利中枢。"
        ),
        "why": (
            "剔除一次性与非经营偶发扰动，识别企业不可持续的利润粉饰，"
            "核准主业真实造血能力。"
        ),
        "provenance": (
            "中国证监会《公开发行证券的公司信息披露解释性公告第1号——非经常性损益》"
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
            "将净资产收益率(ROE)精确分解为五大驱动乘积："
            "税收负担率×利息负担率×息税前利润率×资产周转率×权益乘数。"
        ),
        "why": (
            "结构化解构盈利源动力，精准厘清ROE变动究竟由主营盈利、"
            "财务杠杆还是运营周转所驱动。"
        ),
        "provenance": "经典杜邦财务分析体系五因素扩展模型",
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
            OutputCell(name="dupont_evidence_items", type="list"),
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
            "计算流动比率、速动比率、现金短债比、利息保障倍数，"
            "并推演极端承压情景下的Altman Z-Score与流动性缺口。"
        ),
        "why": (
            "全面评估短期偿债缓冲与长期资本结构稳健性，"
            "提前预警债务违约与资金链断裂断崖风险。"
        ),
        "provenance": (
            "Altman Z-Score破产预测模型及巴塞尔协议流动性覆盖率(LCR)框架"
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
            OutputCell(name="current_ratio", type="number"),
            OutputCell(name="quick_ratio", type="number"),
            OutputCell(name="cash_to_current_debt", type="number"),
            OutputCell(name="altman_z_score", type="number"),
            OutputCell(name="solvency_risk_level", type="text"),
        ],
        "checks": [
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
            "合成现金转化周期(CCC=DIO+DSO-DPO)。"
        ),
        "why": (
            "衡量企业经营营运资金占用效率，"
            "洞察产业链上下游话语权及日常流动性造血循环节奏。"
        ),
        "provenance": (
            "企业营运资本管理理论及现金循环周期(Cash Conversion Cycle)标准模型"
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
            "运用修正琼斯模型测算操纵性应计利润异动。"
        ),
        "why": (
            "穿透权责发生制下的会计应计项异动，识别纸面富贵与利润不含金现象，"
            "防范收益质量劣化。"
        ),
        "provenance": (
            "Dechow & Dichev (2002) 收益质量与应计项异动检验理论及FCFF模型"
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
            OutputCell(name="operating_cf_to_net_income", type="number"),
            OutputCell(name="free_cash_flow_firm", type="number"),
            OutputCell(name="accrual_anomaly_score", type="number"),
            OutputCell(name="earnings_quality_flag", type="text"),
        ],
        "checks": [
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
            "剥离销量增长率与平均单价变动效应。"
        ),
        "why": (
            "穿透收入表象，判定业务扩张究竟由终端放量还是提价驱动，"
            "研判核心业务成长质量。"
        ),
        "provenance": (
            "财政部《企业会计准则第35号——分部报告》(CAS 35) 及量价因素分析法"
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
            "分析购建固定资产支付现金(CapEx)、在建工程结转固定资产进度及折旧摊销年限变动。"
        ),
        "why": "锚定企业在产能扩张周期中所处阶段，研判新增折旧压力与未来业绩释放时点。",
        "provenance": "实业投资与产能周期理论及固定资产折旧会计估计核算",
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
            "核算研发费用占营收比重、研发支出资本化率，"
            "对标专利获批量及新产品收入转化周期。"
        ),
        "why": "防范通过过度资本化虚增当期利润，衡量真实研发硬核科技转化效能。",
        "provenance": (
            "财政部《企业会计准则第6号——无形资产》(CAS 6) 研发支出资本化条件"
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
            "计算3年/5年营业收入与扣非净利润的复合年均增长率(CAGR)，"
            "拟合历史时序回归趋势。"
        ),
        "why": "消除单一年份周期波动假象，确立企业中长期历史内生增长基准轨道。",
        "provenance": (
            "金融统计学几何平均复合年均增长率(CAGR)及对数线性时序回归模型"
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
            "基于历史基准、杜邦分解与营运资本假设，联动推演未来损益、资产负债与现金流，"
            "利用现金/债务平仓(Plug)保持资产负债平衡。"
        ),
        "why": "构建逻辑自洽的前瞻财务沙盘，为内生估值与资本配置提供基准预测三表。",
        "provenance": (
            "华尔街专业财务建模(Wall Street Prep)三表动态配平与循环引用解算框架"
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
                OutputCell(name="dupont_evidence_items", type="list"),
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
            OutputCell(name="forecast_income_statement", type="table"),
            OutputCell(name="forecast_balance_sheet", type="table"),
            OutputCell(name="forecast_cashflow_statement", type="table"),
            OutputCell(name="balance_sheet_plug_zero", type="number"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "forecast_income_statement",
                        "forecast_balance_sheet",
                        "forecast_cashflow_statement",
                        "balance_sheet_plug_zero",
                    ]
                },
            ),
            CheckCell(
                template="identity",
                params={"path": "balance_sheet_plug_zero", "tolerance": 1.0},
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
            "基于三表预测现金流，折算加权平均资本成本(WACC)，"
            "计算显性期FCFF现值与永续增长终值，推导股权内在价值。"
        ),
        "why": (
            "脱离短期二级市场噪音，立足企业终身自由现金流创造能力"
            "核算真实内在底线价值。"
        ),
        "provenance": (
            "纽约大学斯特恩商学院阿斯瓦斯·达摩达兰(Aswath Damodaran)贴现现金流估值专著"
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
            OutputCell(name="enterprise_value_dcf", type="number"),
            OutputCell(name="equity_value_dcf", type="number"),
            OutputCell(name="per_share_intrinsic_value", type="number"),
            OutputCell(name="wacc_used", type="number"),
            OutputCell(name="sensitivity_table", type="table"),
        ],
        "checks": [
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
            "计算PE-TTM、PB-LF、EV/EBITDA乘数，测算所处历史3年/5年分位数，"
            "对标行业可比公司中位数。"
        ),
        "why": "衡量标的在横向行业及纵向历史估值谱系中的溢折价状态与安全边际。",
        "provenance": (
            "相对估值乘数分析理论及中国证监会证券分析师专业估值实务"
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
            "防范大股东质押爆仓导致的控制权变更风险，"
            "识别掏空上市公司与利益输送隐患。"
        ),
        "provenance": (
            "中国证监会《上市公司股东、董监高减持股份的若干规定》"
            "及《企业会计准则第36号——关联方披露》"
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
            "测算商誉占净资产比重，对标并购标的业绩承诺完成率，"
            "测算商誉全额减值对当期损益与净资产的侵蚀厚度。"
        ),
        "why": (
            "提前引爆商誉减值黑天鹅，评估资产负债表潜在存货跌价、坏账及商誉暴雷风险。"
        ),
        "provenance": (
            "财政部《企业会计准则第8号——资产减值》(CAS 8) "
            "及会计监管风险提示第8号——商誉减值"
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
            "提取审计报告中的关键审计事项（KAM）、审计意见类型，"
            "扫描附注中的对外担保、未决诉讼及承诺事项等表外或有负债。"
        ),
        "why": (
            "倾听外部独立审计师的警示哨声，排查表外隐性负债对净资产的刚性求偿风险。"
        ),
        "provenance": (
            "中国注册会计师审计准则第1504号及《企业会计准则第13号——或有事项》"
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
            OutputCell(name="audit_opinion_type", type="text"),
            OutputCell(name="kam_topics_count", type="number"),
            OutputCell(name="kam_matters", type="list"),
            OutputCell(name="external_guarantee_ratio", type="number"),
            OutputCell(name="contingent_liability_risk_verdict", type="text"),
        ],
        "checks": [
            CheckCell(
                template="required",
                params={
                    "keys": [
                        "audit_opinion_type",
                        "kam_topics_count",
                        "kam_matters",
                        "external_guarantee_ratio",
                        "contingent_liability_risk_verdict",
                    ]
                },
            ),
            CheckCell(
                template="non_empty",
                params={"path": "audit_opinion_type"},
            ),
            CheckCell(
                template="list_min",
                params={"path": "kam_matters", "min": 1},
            ),
            CheckCell(
                template="each_has",
                params={
                    "path": "kam_matters",
                    "keys": ["matter", "audit_response", "page"],
                },
            ),
            CheckCell(
                template="quoted",
                params={
                    "path": "kam_matters",
                    "field": "matter",
                    "equals": "收入确认与应收账款减值",
                },
            ),
        ],
    },
    # M19: 竞争壁垒与核心投资假说验证
    {
        "id": "competitive_moat_thesis_synthesis",
        "title": "竞争壁垒与核心投资假说验证",
        "category": "moat",
        "tags": ["经济护城河", "ROIC", "超额收益", "投资假说", "壁垒验证"],
        "does": (
            "综合ROIC与WACC利差，结合无形资产、转换成本、网络效应与成本优势，"
            "对照核心买入假说进行事实证伪。"
        ),
        "why": (
            "将财务指标升华为商业壁垒研判，"
            "验证超额资本回报率的可持续性与竞争护城河深浅。"
        ),
        "provenance": (
            "晨星(Morningstar)经济护城河评级标准及格林沃尔德(Greenwald)竞争优势理论"
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
                OutputCell(name="dupont_evidence_items", type="list"),
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
            "强制聚合报表勾稽、杜邦五步、偿债流动性、收益质量、DCF估值等分析产物，"
            "编排最终研报底稿与证据审计索引。"
        ),
        "why": (
            "作为全流程最终交付单元，消费并核验全部已分析指标，"
            "杜绝跳过核心分析两步直通。"
        ),
        "provenance": "证券投资咨询业务执业规范与合规研报底稿留痕归档指引",
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
                OutputCell(name="dupont_evidence_items", type="list"),
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
            OutputCell(name="structured_dossier", type="text"),
            OutputCell(name="executive_summary", type="text"),
            OutputCell(name="risk_factors", type="list"),
            OutputCell(name="evidence_audit_trail", type="list"),
        ],
        "checks": [
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
                template="covers",
                params={
                    "path": "evidence_audit_trail",
                    "field": "metric",
                    "source_step": "feeder_dupont",
                    "source_path": "dupont_evidence_items",
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
        slot_outputs = spec["feeder_outputs"][slot_name]
        upstream_id = spec["expert_inputs"][slot_name]
        schema = upstream_schemas.get(upstream_id) if upstream_schemas else None
        feeder_steps.append({
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
        })

    target_step = {
        "id": spec["id"],
        "title": spec["title"],
        "does": spec["does"],
        "why": spec["why"],
        "uses": list(spec["inputs"].keys()),
        "rules": [
            {"id": "r1", "text": f"依据：{spec['provenance']}"},
            {"id": "r2", "text": "严禁伪造数据，数值与引文必须精准对齐对应输入源。"},
        ],
        "outputs": [out.model_dump(mode="json") for out in spec["outputs"]],
        "checks": [check.model_dump(mode="json") for check in spec["checks"]],
        "selection": {
            "applies_when": "财务分析执行",
            "required": False,
            "max_calls": 1,
        },
    }

    workdraft = {
        "name": f"方法_{spec['title']}",
        "tagline": "",
        "scope": {"does": spec["does"], "does_not": "", "elsewhere": ""},
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
        selection=MethodSelection(
            applies_when="初始接入", required=True, max_calls=1
        ),
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

        current_expert_draft = WorkdraftBody.model_validate({
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
        })
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

    final_draft = WorkdraftBody.model_validate({
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
    })
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
        target_path
        or Path(__file__).parent / "fixtures/financial-expert-manifest.json"
    )
    manifest, problems = build_financial_expert_manifest()
    if problems:
        raise RuntimeError(f"Compiler problems: {problems}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return path
