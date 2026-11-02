"""Author Method Fixtures for Financial Analysis and Valuation Methods (Complete 20 Methods).
Batch 1: Core Accounting & Diagnostics (M01, M02, M04, M05, M06, M07, M12, M20)
Batch 2: Valuation, Unit Economics & Moat (M03, M08, M09, M14, M15, M19)
Batch 3: Capacity, R&D, 3-Statement Forecast, Governance & Impairment (M10, M11, M13, M16, M17, M18)
"""

from uuid import UUID
from typing import Any, Dict, List

from app.domain.knowbots.compile import compile_manifest
from app.domain.knowbots.methods import (
    BindMethod,
    FreezeMethod,
    MethodDefinition,
    bind,
    freeze,
)
from app.domain.knowbots.workdraft import (
    OutputCell,
    RuleCell,
    StepCell,
    WorkdraftBody,
    cells,
)

# Complete 20 Financial & Valuation Method Specifications
ALL_20_SPECS = [
    # ---------- Batch 1: Core Accounting & Diagnostics (8) ----------
    {
        "id": "fin_statement_restructure",
        "title": "财务三表标准化重构与口径对齐",
        "category": "accounting",
        "tags": ["三表标准化", "口径对齐", "CAS2024"],
        "does": "读取原始披露三表，依CAS2024准则重构列报科目，统一货币计量单位并记录重分类调整溯源。",
        "why": "消除非标科目与列报口径差异，形成横向与跨期可比的基础分析底稿。",
        "provenance": "财政部《企业会计准则第30号——财务报表列报》(CAS 30) 及监管报表披露规范",
        "feeder_outputs": [OutputCell(name="raw_balance_sheet", type="table"), OutputCell(name="raw_income_statement", type="table")],
        "inputs": {"feeder": "raw_statements"},
        "outputs": [
            OutputCell(name="standardized_balance_sheet", type="table"),
            OutputCell(name="standardized_income_statement", type="table"),
            OutputCell(name="reclassification_audit_trail", type="list"),
        ],
    },
    {
        "id": "accounting_statement_reconciliation",
        "title": "报表勾稽平衡与重述校验",
        "category": "accounting",
        "tags": ["勾稽平衡", "资产平表", "追溯重述"],
        "does": "执行资产=负债+所有者权益等式校验(误差绝对值<1.00元)，校验净利润与未分配利润勾稽及前期追溯调整。",
        "why": "验证三表横向纵向强勾稽平衡，排查会计不平衡或重述差错，建立分析可信基石。",
        "provenance": "中国注册会计师审计准则第1501号及三表勾稽平衡公理",
        "feeder_outputs": [OutputCell(name="standardized_balance_sheet", type="table"), OutputCell(name="standardized_income_statement", type="table")],
        "inputs": {"feeder": "standardized_statements"},
        "outputs": [
            OutputCell(name="reconciliation_status", type="text"),
            OutputCell(name="balance_sheet_balance_diff", type="number"),
            OutputCell(name="audit_reconciliations", type="table"),
        ],
    },
    {
        "id": "dupont_five_way_decomposition",
        "title": "杜邦五步分解法",
        "category": "profitability",
        "tags": ["杜邦分析", "ROE", "杠杆驱动", "息税前利润率"],
        "does": "将ROE分解为税务负担率、利息负担率、息税前利润率、资产周转率及权益乘数，评估核心盈利驱动因子。",
        "why": "穿透净资产收益率的杠杆、运营效率与税息影响，识别超额收益的真实来源。",
        "provenance": "经典杜邦分析体系与特许金融分析师(CFA)财务分析框架",
        "feeder_outputs": [OutputCell(name="financial_panel_multi_period", type="table")],
        "inputs": {"feeder": "financial_panel"},
        "outputs": [
            OutputCell(name="roe", type="number"),
            OutputCell(name="operating_margin", type="number"),
            OutputCell(name="asset_turnover", type="number"),
            OutputCell(name="equity_multiplier", type="number"),
            OutputCell(name="dupont_table", type="table"),
        ],
    },
    {
        "id": "solvency_liquidity_stress_check",
        "title": "偿债压力与流动性体检",
        "category": "solvency",
        "tags": ["流动比率", "速动比率", "资产负债率", "压力测试"],
        "does": "计算流动比率、速动比率、现金比率、资产负债率及EBIT利息保障倍数，测算现金流压力抗风险天数。",
        "why": "评估企业短期流动性安全边际与极端现金承压下的刚性兑付能力。",
        "provenance": "信用评级方法论与企业偿债能力分析通用准则",
        "feeder_outputs": [OutputCell(name="standardized_balance_sheet", type="table")],
        "inputs": {"feeder": "standardized_bs"},
        "outputs": [
            OutputCell(name="current_ratio", type="number"),
            OutputCell(name="quick_ratio", type="number"),
            OutputCell(name="debt_to_assets_ratio", type="number"),
            OutputCell(name="stress_test_survival_days", type="number"),
            OutputCell(name="solvency_assessment_summary", type="text"),
        ],
    },
    {
        "id": "working_capital_cash_cycle_analysis",
        "title": "营运资本周转与现金循环周期",
        "category": "efficiency",
        "tags": ["CCC", "DIO", "DSO", "DPO", "营运资本"],
        "does": "计算存货周转天数DIO、应收账款周转天数DSO、应付账款周转天数DPO及现金循环周期CCC=DIO+DSO-DPO。",
        "why": "衡量供应链资金占用与产业链话语权（无偿占用上下游资金程度）。",
        "provenance": "营运资本管理理论与理查兹-劳克林(Richards & Laughlin)现金循环模型",
        "feeder_outputs": [OutputCell(name="multi_period_statements", type="table")],
        "inputs": {"feeder": "multi_period_statements"},
        "outputs": [
            OutputCell(name="dio_inventory_days", type="number"),
            OutputCell(name="dso_receivable_days", type="number"),
            OutputCell(name="dpo_payable_days", type="number"),
            OutputCell(name="ccc_cash_conversion_cycle", type="number"),
            OutputCell(name="channel_bargaining_power_verdict", type="text"),
        ],
    },
    {
        "id": "earnings_quality_accrual_audit",
        "title": "收益质量分析与应计项排雷",
        "category": "quality",
        "tags": ["收益质量", "现金含量", "自由现金流", "应计异象"],
        "does": "测算净利润现金含量(OCF/净利润)、自由现金流FCF=OCF-资本开支、总应计项及其占总资产比重。",
        "why": "识别账面净利润含金量，排查激进会计政策与盈余操纵隐患。",
        "provenance": "斯隆(Sloan 1996)应计异象与企业自由现金流估值前置体检规范",
        "feeder_outputs": [OutputCell(name="cash_flow_statements", type="table")],
        "inputs": {"feeder": "cfs_data"},
        "outputs": [
            OutputCell(name="ocf_to_net_profit_ratio", type="number"),
            OutputCell(name="free_cash_flow", type="number"),
            OutputCell(name="accrual_items_total", type="number"),
            OutputCell(name="earnings_quality_rating", type="text"),
        ],
    },
    {
        "id": "historical_cagr_trend_extrapolation",
        "title": "复合年均增长率测算与趋势外推",
        "category": "growth",
        "tags": ["CAGR", "复合增长", "趋势外推", "平滑预测"],
        "does": "测算营收、归母净利及经营现金流复合年均增长率CAGR，校验增长背离并外推下一期中枢区间。",
        "why": "消除单年基数扰动，平滑测算企业中期增长趋势，为估值假设提供基线支撑。",
        "provenance": "证券投资分析复合增长率统计标准与趋势回归方法",
        "feeder_outputs": [OutputCell(name="summary_series", type="table")],
        "inputs": {"feeder": "time_series"},
        "outputs": [
            OutputCell(name="revenue_cagr", type="number"),
            OutputCell(name="net_profit_cagr", type="number"),
            OutputCell(name="ocf_cagr", type="number"),
            OutputCell(name="extrapolated_forecast_next_year", type="number"),
        ],
    },
    {
        "id": "comprehensive_dossier_formatting",
        "title": "结构化研究底稿与报告成稿编排",
        "category": "reporting",
        "tags": ["投研报告", "工作底稿", "审计溯源", "Markdown"],
        "does": "聚合前序各方法输出，按照券商投研报告标准编排执行摘要、财务体检、风险提示及审计溯源底稿。",
        "why": "形成可审计、数据可溯源、结构标准规范的投研分析成果底稿。",
        "provenance": "中国证券业协会证券投资咨询业务分析报告执业规范",
        "feeder_outputs": [OutputCell(name="dossier_bundle", type="list")],
        "inputs": {"feeder": "dossier_components"},
        "outputs": [
            OutputCell(name="executive_summary", type="text"),
            OutputCell(name="full_dossier_markdown", type="text"),
            OutputCell(name="components_included_count", type="number"),
        ],
    },

    # ---------- Batch 2: Valuation, Unit Economics & Moat (6) ----------
    {
        "id": "non_recurring_items_purification",
        "title": "非经常性损益穿透与扣非还原",
        "category": "quality",
        "tags": ["扣非净利润", "非经常损益", "盈利纯净度", "政府补助"],
        "does": "穿透政府补助、处置损益等偶发项目，结合所得税率扣减还原主营持续经营净利润。",
        "why": "剔除偶然因素扰动，评估主营业务真实核心盈利内核与可持续性。",
        "provenance": "证监会《公开发行证券的公司信息披露解释性公告第1号》",
        "feeder_outputs": [OutputCell(name="standardized_income_statement", type="table"), OutputCell(name="non_recurring_schedule", type="table")],
        "inputs": {"feeder": "income_and_nonrec"},
        "outputs": [
            OutputCell(name="net_profit_parent", type="number"),
            OutputCell(name="non_recurring_items_net", type="number"),
            OutputCell(name="deducted_net_profit", type="number"),
            OutputCell(name="non_recurring_profit_ratio", type="number"),
        ],
    },
    {
        "id": "segment_volume_price_decomposition",
        "title": "分部业务量价拆解",
        "category": "unit_economics",
        "tags": ["量价拆解", "分部业务", "因素替换", "产品结构"],
        "does": "按产品分部拆解销量变动与单价变动对总收入增量的独立贡献，计算分部毛利率。",
        "why": "透视驱动营收增长的微观内核（提价 vs 放量），分析产品结构升级趋势。",
        "provenance": "《企业会计准则第35号——分部报告》及主营业务量价归因模型",
        "feeder_outputs": [OutputCell(name="segment_volume_price_raw", type="table")],
        "inputs": {"feeder": "segment_disclosure"},
        "outputs": [
            OutputCell(name="volume_contribution_amount", type="number"),
            OutputCell(name="price_contribution_amount", type="number"),
            OutputCell(name="product_mix_upgrade_analysis", type="text"),
        ],
    },
    {
        "id": "customer_supplier_concentration_scan",
        "title": "客户与供应商集中度透视",
        "category": "risk",
        "tags": ["集中度", "前五大客户", "前五大供应商", "供应链风险"],
        "does": "提取前五大客户与供应商占比，排查关联方交易比例，计算集中度风险评级。",
        "why": "评估企业对单一渠道或关键供应商的依赖风险，检测供应链稳健性。",
        "provenance": "证监会《公开发行证券的公司信息披露内容与格式准则第2号》",
        "feeder_outputs": [OutputCell(name="counterparty_disclosure", type="table")],
        "inputs": {"feeder": "counterparty_data"},
        "outputs": [
            OutputCell(name="top5_customer_share_total", type="number"),
            OutputCell(name="top5_supplier_share_total", type="number"),
            OutputCell(name="supply_chain_risk_rating", type="text"),
        ],
    },
    {
        "id": "dcf_intrinsic_valuation",
        "title": "企业自由现金流贴现模型 DCF",
        "category": "valuation",
        "tags": ["DCF", "现金流贴现", "WACC", "内在价值", "目标价"],
        "does": "基于显性预测期FCFF与加权平均资本成本WACC，结合永续增长率g测算企业价值EV与每股内在目标价。",
        "why": "立足企业生命周期内在现金创造力，建立严谨的无杠杆现金流绝对估值体系。",
        "provenance": "Aswath Damodaran《投资估价》与CAPM资本资产定价模型",
        "feeder_outputs": [OutputCell(name="dcf_base_assumptions", type="table")],
        "inputs": {"feeder": "dcf_inputs"},
        "outputs": [
            OutputCell(name="wacc_rate", type="number"),
            OutputCell(name="enterprise_value_ev", type="number"),
            OutputCell(name="equity_intrinsic_value", type="number"),
            OutputCell(name="implied_share_price", type="number"),
        ],
    },
    {
        "id": "relative_multiple_valuation",
        "title": "相对估值倍数与历史分位数对标",
        "category": "valuation",
        "tags": ["相对估值", "PE-TTM", "PB", "历史分位数", "同业对标"],
        "does": "计算PE(TTM)、PB倍数，对标近5~10年历史百分位(Percentile)与同业可比公司估值中枢。",
        "why": "锚定当前市场价格在历史估值钟摆中的位置，判定安全边际与相对折溢价。",
        "provenance": "现代投资组合理论相对估值分析规范",
        "feeder_outputs": [OutputCell(name="market_quotes_and_peers", type="table")],
        "inputs": {"feeder": "multiple_inputs"},
        "outputs": [
            OutputCell(name="pe_ttm", type="number"),
            OutputCell(name="pb_ratio", type="number"),
            OutputCell(name="historical_pe_percentile", type="number"),
            OutputCell(name="valuation_watermark_verdict", type="text"),
        ],
    },
    {
        "id": "competitive_moat_thesis_synthesis",
        "title": "竞争壁垒与核心投资假说验证",
        "category": "strategy",
        "tags": ["护城河", "ROIC", "定价权", "投资假说", "证伪指标"],
        "does": "从毛利率定价权、ROIC超额回报利差(ROIC-WACC)、特许资源及客户黏性综合判定护城河等级并建立证伪清单。",
        "why": "验证商业模式超额收益的持续性，为长期投资价值提供定性与定量交叉支持。",
        "provenance": "晨星(Morningstar)经济护城河分析评级理论与反脆弱投资假说框架",
        "feeder_outputs": [OutputCell(name="long_term_profile", type="table")],
        "inputs": {"feeder": "moat_inputs"},
        "outputs": [
            OutputCell(name="moat_grade_rating", type="text"),
            OutputCell(name="pricing_power_score", type="number"),
            OutputCell(name="roic_wacc_spread", type="number"),
            OutputCell(name="moat_synthesis_summary", type="text"),
        ],
    },

    # ---------- Batch 3: Capacity, R&D, Forecasting, Governance & Impairment (6) ----------
    {
        "id": "capex_capacity_cycle_tracker",
        "title": "资本开支与产能释放周期追踪",
        "category": "capacity",
        "tags": ["资本开支", "CAPEX", "在建工程转固", "产能周期", "新增折旧"],
        "does": "追踪重大在建工程进度、固定资产原值与折旧速度、CAPEX扩张节奏，推演产能投产周期对未来折旧和成本的挤压。",
        "why": "预判重资产企业固定资产扩张节奏对利润率的周期性扰动。",
        "provenance": "《企业会计准则第4号——固定资产》及产能投资周期理论",
        "feeder_outputs": [OutputCell(name="cip_and_fixed_assets_schedule", type="table")],
        "inputs": {"feeder": "capex_and_cip_data"},
        "outputs": [
            OutputCell(name="capex_to_revenue_ratio", type="number"),
            OutputCell(name="cip_turn_fixed_assets_amount", type="number"),
            OutputCell(name="capacity_cycle_outlook", type="text"),
        ],
    },
    {
        "id": "rnd_capitalization_efficiency_eval",
        "title": "研发投入资本化与产出效能评估",
        "category": "rnd",
        "tags": ["研发投入", "资本化率", "研发效能", "无形资产摊销"],
        "does": "拆解研发投入总额、研发费用化比例与资本化比例、研发人员人均产出，评估资本化条件合规性与无形资产未来摊销压力。",
        "why": "排查激进资本化粉饰当期利润风险，评估科技创新研发转化为业绩的实际效率。",
        "provenance": "《企业会计准则第6号——无形资产》（开发支出资本化标准）",
        "feeder_outputs": [OutputCell(name="rnd_notes_disclosure", type="table")],
        "inputs": {"feeder": "rnd_expenditure_notes"},
        "outputs": [
            OutputCell(name="rnd_total_expenditure", type="number"),
            OutputCell(name="rnd_capitalization_ratio", type="number"),
            OutputCell(name="rnd_expense_ratio", type="number"),
            OutputCell(name="rnd_efficiency_verdict", type="text"),
        ],
    },
    {
        "id": "three_statement_linked_forecasting",
        "title": "三表联动预测模型",
        "category": "forecasting",
        "tags": ["财务建模", "三表联动", "资产平表", "配平项Plug"],
        "does": "基于核心营收增速、毛利率、期间费用率及营运资本周转天数假设，联动预测未来3年三表并强制配平。",
        "why": "建立具有强会计勾稽闭环的中期动态预测底稿，杜绝割裂预测。",
        "provenance": "投资银行财务模型(Financial Modeling)三表配平公理",
        "feeder_outputs": [OutputCell(name="historical_base_and_driver_assumptions", type="table")],
        "inputs": {"feeder": "forecasting_assumptions_and_base"},
        "outputs": [
            OutputCell(name="forecasted_revenue_3y", type="table"),
            OutputCell(name="forecasted_net_profit_3y", type="table"),
            OutputCell(name="balance_sheet_plug_cash", type="number"),
            OutputCell(name="statements_linked_balance_check", type="text"),
        ],
    },
    {
        "id": "pledge_reduction_related_transaction_scan",
        "title": "股权质押、减持与重大关联交易穿透",
        "category": "governance",
        "tags": ["股权质押", "高管减持", "关联交易", "资金占用", "利益输送"],
        "does": "排查大股东及董监高股权质押比例、平仓线安全垫、重要股东增减持及重大关联交易公允性与非经营性资金往来。",
        "why": "识别公司治理结构风险、控股股东流动性困境与潜在资金侵占隐患。",
        "provenance": "证监会《上市公司监管指引第8号——上市公司资金往来与对外担保》",
        "feeder_outputs": [OutputCell(name="governance_and_insider_disclosure", type="table")],
        "inputs": {"feeder": "governance_and_pledge_notes"},
        "outputs": [
            OutputCell(name="pledged_shares_ratio", type="number"),
            OutputCell(name="insider_reduction_amount", type="number"),
            OutputCell(name="related_party_receivables_ratio", type="number"),
            OutputCell(name="governance_risk_rating", type="text"),
        ],
    },
    {
        "id": "goodwill_asset_impairment_stress_test",
        "title": "商誉减值与资产质量压力测试",
        "category": "impairment",
        "tags": ["商誉减值", "坏账准备", "资产质量", "净资产压力测试"],
        "does": "测算商誉占净资产比例、并购标的历史业绩承诺完成度，针对存货与应收账款执行极端情景坏账与跌价压力测算。",
        "why": "排查并购后遗症与资产负债表虚胖，提前暴露业绩暴雷潜在隐患。",
        "provenance": "《企业会计准则第8号——资产减值》及《会计监管风险提示第8号》",
        "feeder_outputs": [OutputCell(name="impairment_and_goodwill_disclosure", type="table")],
        "inputs": {"feeder": "asset_impairment_notes"},
        "outputs": [
            OutputCell(name="goodwill_to_equity_ratio", type="number"),
            OutputCell(name="goodwill_stress_impairment_loss", type="number"),
            OutputCell(name="accounts_receivable_bad_debt_provision", type="number"),
            OutputCell(name="asset_quality_rating", type="text"),
        ],
    },
    {
        "id": "kam_contingent_liability_audit",
        "title": "关键审计事项与或有负债排查",
        "category": "audit",
        "tags": ["KAM", "审计意见", "对外担保", "未决诉讼", "或有负债"],
        "does": "提取审计报告中的关键审计事项（KAM）、审计意见类型，扫描附注中的对外担保、未决诉讼及承诺事项等表外或有负债。",
        "why": "倾听外部独立审计师的警示哨声，排查表外隐性负债对净资产的刚性求偿风险。",
        "provenance": "中国注册会计师审计准则第1504号及《企业会计准则第13号——或有事项》",
        "feeder_outputs": [OutputCell(name="audit_report_and_contingencies", type="table")],
        "inputs": {"feeder": "audit_report_fulltext"},
        "outputs": [
            OutputCell(name="audit_opinion_type", type="text"),
            OutputCell(name="kam_topics_count", type="number"),
            OutputCell(name="external_guarantee_ratio", type="number"),
            OutputCell(name="contingent_liability_risk_verdict", type="text"),
        ],
    },
]

# Aliases for Batches
BATCH1_SPECS = ALL_20_SPECS[:8]
BATCH2_SPECS = ALL_20_SPECS[8:14]
BATCH3_SPECS = ALL_20_SPECS[14:]


def make_author_method_definition(spec: Dict[str, Any]) -> MethodDefinition:
    """Creates an author-owned frozen MethodDefinition from spec."""
    output_names = [out.name for out in spec["outputs"]]
    
    feeder_step = {
        "id": "feeder",
        "title": "数据前置接入",
        "does": "提供标准化数据源。",
        "selection": {"applies_when": "初始加载", "required": True, "max_calls": 1},
        "outputs": [out.model_dump(mode="json") for out in spec["feeder_outputs"]],
        "checks": [{"template": "required", "params": {"keys": [o.name for o in spec["feeder_outputs"]]}}],
    }
    
    target_step = {
        "id": spec["id"],
        "title": spec["title"],
        "does": spec["does"],
        "why": spec["why"],
        "uses": ["feeder"],
        "rules": [
            {"id": "r1", "text": f"依据：{spec['provenance']}"},
            {"id": "r2", "text": "严禁伪造数据，数值必须精准对齐对应输入源。"},
        ],
        "outputs": [out.model_dump(mode="json") for out in spec["outputs"]],
        "checks": [
            {"template": "required", "params": {"keys": output_names}},
        ],
        "selection": {"applies_when": "财务分析执行", "required": False, "max_calls": 1},
    }
    workdraft = {
        "name": f"方法_{spec['title']}",
        "tagline": "",
        "scope": {"does": spec["does"], "does_not": "", "elsewhere": ""},
        "data_picks": [],
        "steps": [feeder_step, target_step],
        "planning": {
            "method_text": f"按需执行{spec['title']}，严格遵循准则与出处要求。",
            "final_units": [spec["id"]],
            "max_steps": 5,
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


def build_financial_expert_manifest(specs: List[Dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[Any]]:
    """Assembles a full financial expert workdraft binding methods via bind()."""
    target_specs = specs if specs is not None else ALL_20_SPECS
    definitions = {spec["id"]: make_author_method_definition(spec) for spec in target_specs}
    
    # 1 consolidated intake step covering all feeder outputs across all 20 methods
    intake_outputs = []
    seen = set()
    for spec in target_specs:
        for out in spec["feeder_outputs"]:
            if out.name not in seen:
                intake_outputs.append(out.model_dump(mode="json"))
                seen.add(out.name)
                
    intake_step = {
        "id": "financial_data_intake",
        "title": "财务数据前置总接入",
        "does": "读取并供给全部财务分析基础三表、附注及外部行情数据。",
        "selection": {"applies_when": "初始接入", "required": True, "max_calls": 1},
        "outputs": intake_outputs,
        "checks": [{"template": "required", "params": {"keys": [o["name"] for o in intake_outputs]}}],
    }
    
    expert_steps = [intake_step]
    method_refs = {}
    method_sources = {}
    
    for idx, spec in enumerate(target_specs):
        m_id = spec["id"]
        expert_steps.append({
            "id": m_id,
            "title": spec["title"],
            "does": spec["does"],
            "why": spec["why"],
            "uses": ["financial_data_intake"],
            "rules": [{"id": "r1", "text": f"依据：{spec['provenance']}"}],
            "outputs": [out.model_dump(mode="json") for out in spec["outputs"]],
            "checks": [{"template": "required", "params": {"keys": [out.name for out in spec["outputs"]]}}],
            "selection": {"applies_when": "规划执行", "required": False, "max_calls": 1},
        })
        
    expert_body_raw = {
        "name": "全面财务与估值分析专家",
        "tagline": "基于20项不可变作者方法库的财务三表重构、估值推演、公司治理与护城河体检专家",
        "scope": {
            "does": "对标的进行标准化三表重构、勾稽校验、杜邦分解、流动性体检、营运周期、收益质量、DCF估值、相对估值、三表预测、产能追踪、研发评估、商誉减值与治理穿透综合研判",
            "does_not": "不代客理财，不提供内幕交易信息",
            "elsewhere": "",
        },
        "data_picks": [],
        "steps": expert_steps,
        "planning": {
            "method_text": "接收到公司代码与财报数据时，由数据接入步骤供给数据，依次编排三表重构、勾稽平表、杜邦五步、三表预测、DCF估值及综合底稿成稿。",
            "final_units": ["comprehensive_dossier_formatting"],
            "max_steps": 25,
            "max_replans": 2,
            "reserve": "0.15",
            "input_max_chars": 64000,
        },
    }
    
    # Bind each method through official bind()
    current_body = WorkdraftBody.model_validate(expert_body_raw)
    
    for idx, spec in enumerate(target_specs):
        m_id = spec["id"]
        defn = definitions[m_id]
        digest = defn.digest()
        method_uuid = UUID(int=1000 + idx)
        slot_name = list(spec["inputs"].values())[0]
        
        bind_cmd = BindMethod(
            request_id=UUID(int=5000 + idx),
            revision=1,
            alias=m_id,
            digest=digest,
            inputs={slot_name: "financial_data_intake"},
            required=False,
            max_calls=1,
        )
        bound_step = bind(defn, current_body, bind_cmd)
        
        for i, s in enumerate(current_body.steps):
            if s.id == m_id:
                expert_steps[i] = bound_step.model_dump(mode="json")
                break
                
        ref = {
            "method_id": str(method_uuid),
            "version": 1,
            "digest": digest,
            "inputs": {slot_name: "financial_data_intake"},
        }
        method_refs[m_id] = ref
        method_sources[m_id] = {
            "reference": ref,
            "definition": defn.document(),
        }
        
    expert_body_raw["steps"] = expert_steps
    expert_body_raw["method_refs"] = method_refs
    parsed = WorkdraftBody.model_validate(expert_body_raw)
    
    sources = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed))
    }
    
    return compile_manifest(
        parsed,
        sources,
        pack_id="comprehensive-financial-expert-v1",
        version="1",
        runtime="knowbench",
        fallback_name="comprehensive-financial-expert",
        method_sources=method_sources,
    )
