"""数据需求声明与测试绑定（SDD 0042 第五节，契约 knowbot-manifest/v1 的 `data_needs`）。

专家只是逻辑、不绑数据（10-23）：孵化在清单里写「数据需求声明」——要请专家的人提供哪几样、
什么格式、几份、必填还是可选、表格类要有哪几张表哪几列；测试按声明从这个用户的知识库绑数据
（`data_bindings`，每份写 `need` 对上声明里的哪一样）。交 knowmart 只带声明，不带绑定。

声明的规矩与 knowmart 的外层模型一致（knowmart `app/domain/knowbots/manifest.py`），
这里先核，免得交付时才被拒。没写声明的是旧格式，绑定照旧按作者的数据核。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

NEED_KEY = r"^[a-z][a-z0-9_]{0,39}$"
DataFormat = Literal["pdf", "excel", "csv", "text"]
TABULAR: frozenset[str] = frozenset({"excel", "csv"})
MAX_NEEDS = 20
MAX_DATA_ITEMS = (
    64  # 一次请专家最多选这么多份（同 knowmart、knowbase 一条运行授权的上限）
)
# 数据的内容类型 → 声明里的格式（knowlab 收的几种，datasets.KINDS）
FORMAT_OF: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "application/vnd.ms-excel": "excel",
    "text/csv": "csv",
    "text/plain": "text",
    "text/markdown": "text",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataTable(_Strict):
    sheet: str | None = Field(default=None, min_length=1, max_length=100)
    columns: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = Field(
        min_length=1, max_length=50
    )


class DataNeed(_Strict):
    key: str = Field(pattern=NEED_KEY)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    formats: tuple[DataFormat, ...] = Field(min_length=1, max_length=4)
    required: bool = True
    min_count: int = Field(default=1, ge=1, le=MAX_DATA_ITEMS)
    max_count: int = Field(default=1, ge=1, le=MAX_DATA_ITEMS)
    tables: tuple[DataTable, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def _consistent(self) -> DataNeed:
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("a format may be listed only once")
        if self.max_count < self.min_count:
            raise ValueError("max_count must not be below min_count")
        if self.tables and not set(self.formats) <= TABULAR:
            raise ValueError("tables can be required only of Excel or CSV data")
        return self


def format_of(content_type: str) -> str | None:
    return FORMAT_OF.get(content_type.split(";")[0].strip().lower())


def parse_needs(raw: Any) -> tuple[list[DataNeed], list[str]]:
    """（读出的声明，不合规矩的地方）。没写就是空的声明。"""
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
    keys = [n.key for n in needs]
    if len(keys) != len(set(keys)):
        problems.append("each data need needs its own key")
    if sum(n.max_count for n in needs) > MAX_DATA_ITEMS:
        problems.append(f"the data needs ask for more than {MAX_DATA_ITEMS} items")
    return needs, problems


def bound_problems(
    needs: list[DataNeed], bindings: Any, formats: dict[str, str | None]
) -> list[str]:
    """测试绑定按声明核：每份写对应哪一样、格式是那一样收的、每样的份数在范围里。

    没写声明（旧格式）时，绑定上不许写 need。formats：数据 id → 格式（认不出是 None）。
    绑定是不是这个用户自己的、版本与摘要对不对，由 datasets.binding_problems 核。
    """
    items = (
        [b for b in bindings if isinstance(b, dict)]
        if isinstance(bindings, list)
        else []
    )
    if not needs:
        if any(b.get("need") is not None for b in items):
            return ["a data binding names a need, but no data needs are declared"]
        return []
    by_key = {n.key: n for n in needs}
    counts = dict.fromkeys(by_key, 0)
    problems: list[str] = []
    for b in items:
        need = by_key.get(str(b.get("need")))
        if need is None:
            problems.append("each data binding must name a declared need")
            continue
        counts[need.key] += 1
        if formats.get(str(b.get("dataset_id"))) not in need.formats:
            problems.append(
                f"a data binding for {need.key} is not in a format it takes"
            )
    for key, n in counts.items():
        need = by_key[key]
        if (need.required or n) and not need.min_count <= n <= need.max_count:
            problems.append(
                f"{key} takes {need.min_count} to {need.max_count} items, {n} are bound"
            )
    return problems


def for_knowmart(manifest: dict[str, Any]) -> dict[str, Any]:
    """交 knowmart 的那份：写了声明的不带测试绑定；旧格式原样（摘要不变）。"""
    if manifest.get("data_needs"):
        return {k: v for k, v in manifest.items() if k != "data_bindings"}
    return manifest
