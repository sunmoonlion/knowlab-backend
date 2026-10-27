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
    "annual_report",
    "semiannual_report",
    "q1_report",
    "q3_report",
    "announcement",
    "prospectus",
    "policy",
    "statistics",
    "webpage",
    "other_table",
    "other_text",
]
DataFormat = Literal["pdf", "excel", "csv", "text", "markdown", "html"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataTable(_Strict):
    """表格类数据要有的一张表与几列（按表头的字认，不按位置）；CSV 不写表名。"""

    sheet: str | None = Field(default=None, min_length=1, max_length=100)
    columns: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = Field(
        min_length=1, max_length=50
    )


class PeriodRule(_Strict):
    """固定期间（start、end），最近 N 个完整的报告期（count，可加截至任务日期已披露），
    或不限期间（none，只给不是定期报告的）。"""

    kind: Literal["fixed", "latest", "none"]
    start: date | None = None
    end: date | None = None
    count: int | None = Field(default=None, ge=1, le=10)
    disclosed_by_task_date: bool = False


class DataNeed(_Strict):
    """声明模板里的一样：类型、期间规则、格式、份数、必填、要的读法、来源限制、
    多候选时的优先策略、表格类的表与列；口径与说明只展示。"""

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
