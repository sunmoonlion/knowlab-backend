"""运行方读用户数据的契约 `knowbot-data/v1`（SDD 0027 §3.5）。

knowlab 是提供方：分身（或测试中的草稿）在运行方跑的时候，运行方先按自己的委托核过，再用
服务身份 `knowlab:read` 来读。用途二选一：

- `knowbot`：用户请来的分身。所有者、分身 id、版本、清单摘要要和交付记录对得上；
- `test`：knowlab 发起的测试。这次测试要正在跑。

只能读这一版清单 `data_bindings` 里的、属于草稿所有者的、没删的数据。
读出来的是文字，按页给。

- `POST /api/internal/v1/knowlab/knowbot-data/list`：列出这次能读的数据；
- `POST /api/internal/v1/knowlab/knowbot-data/read`：读一份数据的一页。

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
