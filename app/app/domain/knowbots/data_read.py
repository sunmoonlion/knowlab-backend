"""运行方读用户数据的契约 `knowbot-data/v1`（SDD 0027 §3.5）。

knowlab 是提供方：分身（或测试中的草稿）在运行方跑的时候，运行方先按自己的委托核过，再用
服务身份 `knowlab:read` 来读。用途二选一：

- `knowbot`：用户请来的分身。所有者、分身 id、版本、清单摘要要和交付记录对得上；
- `test`：knowlab 发起的测试。这次测试要正在跑。

只能读这一版清单 `data_bindings` 里的、属于草稿所有者的、没删的数据。
读出来的是文字，按页给；CSV、Excel 还能按格子读（SDD 0030 §3.1），给计算工具按位置取数。

- `POST /api/internal/v1/knowlab/knowbot-data/list`：列出这次能读的数据；
- `POST /api/internal/v1/knowlab/knowbot-data/read`：读一份数据的一页；
- `POST /api/internal/v1/knowlab/knowbot-data/table`：读表格数据的一张表
  （行标签、列标签、原文，按页）；
- `POST /api/internal/v1/knowlab/knowbot-data/cells`：按（数据、表、行标签、列标签）
  取格子的值。

格子的数一律是十进制字符串，不经浮点。

不符一律 404，不区分「没有」与「不是你的」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from app.domain.knowbots.test_run import RUN_ID, Strict

CONTRACT = "knowbot-data/v1"
PAGE_CHARS = 20_000  # 一页最多这么多字：够读，又不撑满模型的上下文
MAX_TEXT_CHARS = 5_000_000  # 一份数据抽出的文字最多这么多，超出截断并说明
KNOWBOT_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
DATASET_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
TABLE_PAGE_CELLS = 4_000  # 表格一页最多这么多格（行数按列数折算，至少一行）
MAX_CELLS_PER_REQUEST = 500  # 一次取格子最多这么多个（同 0030 §3.2 一次计算的输入上限）
DECIMAL = r"^-?[0-9]+(\.[0-9]+)?$"
Label = Annotated[str, Field(min_length=1, max_length=512)]


class KnowbotPurpose(Strict):
    """用户请来的分身：运行方从已认证的会话与钉住的清单取这几项。"""

    kind: Literal["knowbot"]
    owner_subject: str = Field(pattern=r"^[A-Za-z0-9._:@-]{1,512}$")
    knowbot_id: str = Field(pattern=KNOWBOT_ID)
    version: str = Field(pattern=r"^[1-9][0-9]{0,5}$")
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RunPurpose(Strict):
    """knowlab 发起的测试：数据的所有者由 knowlab 按草稿认，运行方不用给。"""

    kind: Literal["test"]
    run_id: str = Field(pattern=RUN_ID)


Purpose = Annotated[KnowbotPurpose | RunPurpose, Field(discriminator="kind")]


class ListRequestV1(Strict):
    contract: Literal["knowbot-data/v1"]
    purpose: Purpose


class ReadRequestV1(Strict):
    contract: Literal["knowbot-data/v1"]
    purpose: Purpose
    dataset_id: str = Field(pattern=DATASET_ID)
    page: int = Field(default=1, ge=1, le=1_000_000)


class TableRequestV1(Strict):
    contract: Literal["knowbot-data/v1"]
    purpose: Purpose
    dataset_id: str = Field(pattern=DATASET_ID)
    sheet: str | None = Field(
        default=None, min_length=1, max_length=512
    )  # 不给就是第一张
    page: int = Field(default=1, ge=1, le=1_000_000)


class CellRef(Strict):
    """一个格子的位置。

    运行方的地址写法 `<数据 id>:<表>!<行标签>@<列标签>` 由运行方拆成这四项。
    """

    dataset_id: str = Field(pattern=DATASET_ID)
    sheet: Label
    row: Label
    column: Label


class CellsRequestV1(Strict):
    contract: Literal["knowbot-data/v1"]
    purpose: Purpose
    cells: tuple[CellRef, ...] = Field(min_length=1, max_length=MAX_CELLS_PER_REQUEST)


class DatasetEntry(Strict):
    dataset_id: str
    name: str
    content_type: str
    size_bytes: int
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ListResultV1(Strict):
    contract: Literal["knowbot-data/v1"] = CONTRACT
    datasets: tuple[DatasetEntry, ...]


class Citation(Strict):
    """引用这份数据时写上：哪份、哪一版、内容摘要、第几页。"""

    dataset_id: str
    version: str
    sha256: str
    page: int
    pages: int


class ReadResultV1(Strict):
    contract: Literal["knowbot-data/v1"] = CONTRACT
    dataset_id: str
    name: str
    content_type: str
    page: int = Field(ge=1)
    pages: int = Field(ge=1)
    text: str
    # 抽出的文字超过上限被截断了，或者抽不出字（比如扫描的 PDF）：照实说，不让模型猜
    truncated: bool = False
    note: str | None = None
    citation: Citation


class TableRow(Strict):
    label: str
    values: dict[str, str]  # 列标签 → 原文；空格子不列


class TableResultV1(Strict):
    contract: Literal["knowbot-data/v1"] = CONTRACT
    dataset_id: str
    name: str
    content_type: str
    sheets: tuple[str, ...]  # 这份数据里所有的表
    sheet: str
    columns: tuple[str, ...]
    page: int = Field(ge=1)
    pages: int = Field(ge=1)
    rows: tuple[TableRow, ...]
    note: str | None = None
    citation: Citation


class CellValue(Strict):
    """按请求的次序一一对应。

    没有这一行或这一列：found 为 false；有但读不成数：number 为空。
    """

    dataset_id: str
    sheet: str
    row: str
    column: str
    found: bool
    raw: str | None = None
    number: str | None = Field(default=None, pattern=DECIMAL)
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CellsResultV1(Strict):
    contract: Literal["knowbot-data/v1"] = CONTRACT
    cells: tuple[CellValue, ...]


@dataclass(frozen=True)
class Cell:
    """表格里的一格：原文，能读成数时的十进制字符串。"""

    raw: str
    number: str | None = None


@dataclass(frozen=True)
class Row:
    label: str
    cells: dict[str, Cell]


@dataclass(frozen=True)
class Sheet:
    name: str
    columns: tuple[str, ...]
    rows: tuple[Row, ...]

    def row(self, label: str) -> Row | None:
        return next((r for r in self.rows if r.label == label), None)


@dataclass(frozen=True)
class Tables:
    """一份表格数据解析出的所有表。"""

    sheets: tuple[Sheet, ...]

    def sheet(self, name: str | None) -> Sheet | None:
        if name is None:
            return self.sheets[0] if self.sheets else None
        return next((s for s in self.sheets if s.name == name), None)


class TableUnreadable(Exception):
    """这份数据不是表格，或读不出来（消息可以给运行方看）。"""


def table_page(sheet: Sheet, page: int) -> tuple[tuple[Row, ...], int]:
    """第 page 页的行与总页数；一页的行数按列数折算，空表也算一页。"""
    per_page = max(1, TABLE_PAGE_CELLS // max(1, len(sheet.columns)))
    pages = max(1, -(-len(sheet.rows) // per_page))
    start = (page - 1) * per_page
    return sheet.rows[start : start + per_page], pages


@dataclass(frozen=True)
class Extracted:
    """一份数据抽出来的文字。"""

    text: str
    truncated: bool = False
    note: str | None = None


def paginate(text: str, page: int) -> tuple[str, int]:
    """第 page 页（从 1 起）与总页数。空文字也算一页；页码超出就回空的最后一页之后。"""
    pages = max(1, -(-len(text) // PAGE_CHARS))
    if page > pages:
        return "", pages
    start = (page - 1) * PAGE_CHARS
    return text[start : start + PAGE_CHARS], pages
