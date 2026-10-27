"""数据需求声明 v2：照 knowbase 的契约 `knowbot-data-needs/v2` 写的同一份模型。

作者在 knowlab 写声明模板（清单 v2 的 `data_needs`）。真源在
`knowbase-app/contracts/knowbot-data-needs/v2/`，本仓 `contracts/` 的锁钉住它的摘要；
并列检出 knowbase-app 时，测试核这里生成的 schema 与真源一致（不计说明文字）。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DocType = Literal[
    "disclosure",
    "research",
    "news",
    "briefing",
    "policy",
    "statistics",
    "other",
    "annual_report",
    "semiannual_report",
    "q1_report",
    "q3_report",
    "interim_announcement",
    "prospectus",
    "listing_announcement",
    "ir_activity",
    "ir_qa",
    "research_morning",
    "research_macro",
    "research_strategy",
    "research_industry",
    "research_company",
    "research_quant",
    "research_market",
    "research_fixed_income",
    "research_commodity",
    "research_derivatives",
    "research_fund",
    "research_fx",
    "research_reits",
    "research_esg",
    "research_other",
    "financial_news",
    "recommended_news",
    "negative_news",
    "brief",
    "law",
    "administrative_regulation",
    "departmental_rule",
    "normative_document",
    "local_regulation",
    "judicial_interpretation",
    "policy_interpretation",
    "business_rule",
    "regulatory_letter",
    "statistical_bulletin",
    "statistical_data",
    "census",
    "webpage",
    "other_table",
    "other_text",
]
DataFormat = Literal["pdf", "excel", "csv", "text", "markdown", "html"]
Edition = Literal["full", "summary", "english"]
Topic = Literal[
    "results",
    "ipo",
    "refinancing",
    "dividend",
    "meetings",
    "governance",
    "operations",
    "restructuring",
    "equity",
    "trading",
    "intermediary",
    "supervision",
]
Industry = Literal[
    "石油石化",
    "煤炭",
    "有色金属",
    "电力及公用事业",
    "钢铁",
    "基础化工",
    "建筑",
    "建材",
    "轻工制造",
    "机械",
    "电力设备",
    "国防军工",
    "汽车",
    "商贸零售",
    "消费者服务",
    "家电",
    "纺织服装",
    "医药",
    "食品饮料",
    "农林牧渔",
    "银行",
    "非银行金融",
    "房地产",
    "交通运输",
    "电子元器件",
    "通信",
    "计算机",
    "传媒",
    "综合",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataTable(_Strict):
    """表格类数据要有的一张表与几列（按表头的字认，不按位置）；CSV 不写表名。"""

    sheet: str | None = Field(default=None, min_length=1, max_length=100)
    columns: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = Field(
        min_length=1, max_length=50
    )


class PeriodRule(_Strict):
    """固定期间（start、end）；定期报告最近 N 个完整的报告期（latest + count，
    可加截至任务日期已披露）；其余类型最近 N 个月内披露的（recent + months），
    或不限期间（none）。定期报告的期间是报告期，其余类型的期间是披露时间窗
    （SDD 0049）。"""

    kind: Literal["fixed", "latest", "recent", "none"]
    start: date | None = None
    end: date | None = None
    count: int | None = Field(default=None, ge=1, le=10)
    disclosed_by_task_date: bool = False
    months: int | None = Field(default=None, ge=1, le=60)


class DataNeed(_Strict):
    """声明模板里的一样：类型（细类，或只写大类）、期间规则、版本形态、格式、份数、
    必填、要的读法、来源限制、多候选时的优先策略、表格类的表与列；事项与行业写了就是
    缩小范围；口径与说明只展示。"""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    doc_type: DocType
    period: PeriodRule
    formats: tuple[DataFormat, ...] = Field(min_length=1, max_length=6)
    required: bool = True
    min_count: int = Field(default=1, ge=1, le=200)
    max_count: int = Field(default=1, ge=1, le=200)
    reading: Literal["original", "text", "pages", "table"] = "original"
    sources: tuple[Literal["disclosure", "collected", "upload", "output"], ...] = Field(
        default=(), max_length=4
    )
    priority: Literal["public_first", "upload_first", "newest"] | None = None
    tables: tuple[DataTable, ...] = Field(default=(), max_length=20)
    caliber: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=500)
    edition: Edition = "full"
    topics: tuple[Topic, ...] = Field(default=(), max_length=12)
    industry: Industry | None = None


MAX_NEEDS = 20
TABULAR: frozenset[str] = frozenset({"excel", "csv"})


def parse_needs_v2(raw: Any) -> tuple[list[DataNeed], list[str]]:
    """（读出的声明，不合规矩的地方）。没写就是空的声明（不要数据的专家）。"""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["data_needs must be a list"]
    if len(raw) > MAX_NEEDS:
        return [], [f"at most {MAX_NEEDS} data needs"]
    needs: list[DataNeed] = []
    problems: list[str] = []
    for i, item in enumerate(raw):
        try:
            needs.append(DataNeed.model_validate(item))
        except ValidationError as exc:
            where = sorted(
                {".".join(str(p) for p in e["loc"]) or "need" for e in exc.errors()}
            )
            problems.append(f"data_needs[{i}] is invalid: " + ", ".join(where[:5]))
    if problems:
        return [], problems
    if len({n.key for n in needs}) != len(needs):
        problems.append("each data need needs its own key")
    for n in needs:
        if n.max_count < n.min_count:
            problems.append(f"{n.key}: max_count must not be below min_count")
        if n.tables and not set(n.formats) <= TABULAR:
            problems.append(f"{n.key}: tables can be required only of Excel or CSV")
    return needs, problems
