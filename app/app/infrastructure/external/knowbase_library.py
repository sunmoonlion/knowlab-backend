"""knowbase 的知识库栏（契约 user-data/v2，SDD 0043、0044 第二节）：凭作者本人令牌。

用于 knowlab web端的「知识库」栏（SDD 0046 第六节：只展示给了 knowlab 的数据，链到
knowbase 去管）与工作稿里挑数据（专家孵化方案第 7.1、8.2 节）：

- `GET /columns/knowlab`：作者展示给 knowlab、自己还有使用权的资料。knowbase 只许
  令牌的代办应用（`azp`）读自己那一栏；
- `GET /materials/{资料}/versions/{版本}/renditions`：一版的各种读法与就绪没有。

只读描述与读法，不取内容。令牌只在内存里，不进日志。资料不在回 404
（material_not_found），别的拒绝（令牌、代办应用不认）当 knowbase 那头的配置问题回 502
（knowbase_refused）；不可达抛 ServiceUnavailableError。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.errors.exceptions import BadGatewayError, NotFoundError
from app.domain.knowbots.incubation import TableFormat
from app.infrastructure.external.knowbase_client import (
    KnowbaseClient,
    KnowbaseRefused,
    _segment,
)

COLUMN = "knowlab"


class FormatTable(TableFormat):
    # 提供方可增响应字段；保存时只投影孵化契约认识的字段。
    model_config = ConfigDict(extra="ignore")


class MaterialFormat(BaseModel):
    material_id: str
    version_no: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    tables: list[FormatTable] = Field(max_length=50)


class KnowbaseLibrary(KnowbaseClient):
    BASE = "/api/internal/v2/user-data"

    async def read_format(
        self, user_token: str, material_id: str, version_no: int
    ) -> dict[str, Any] | None:
        path = f"/materials/{_segment(material_id)}/versions/{version_no}/format"
        try:
            _, body, _ = await self._send("GET", path, user_token)
        except KnowbaseRefused as exc:
            if exc.status == 422 and exc.code == "format_unreadable":
                return None  # 这一份表轴读不出：表轴未知，不挡整次挑选（账 186）
            raise _refused(exc) from None
        try:
            parsed = MaterialFormat.model_validate(body)
        except ValidationError:
            raise BadGatewayError(
                "knowbase answered format outside user-data/v2",
                code="knowbase_contract_invalid",
            ) from None
        return parsed.model_dump(mode="json")

    async def _get(self, path: str, user_token: str) -> Any:
        try:
            _, body, _ = await self._send("GET", path, user_token)
        except KnowbaseRefused as exc:
            raise _refused(exc) from None
        return body

    async def column(self, user_token: str) -> list[dict[str, Any]]:
        body = await self._get(f"/columns/{COLUMN}", user_token)
        if not isinstance(body, list):
            raise BadGatewayError(
                "knowbase answered the column outside user-data/v2",
                code="knowbase_contract_invalid",
            )
        return [e for e in body if isinstance(e, dict)]

    async def renditions(
        self, user_token: str, material_id: str, version_no: int
    ) -> list[dict[str, Any]]:
        body = await self._get(
            f"/materials/{_segment(material_id)}/versions/{int(version_no)}/renditions",
            user_token,
        )
        if not isinstance(body, list):
            raise BadGatewayError(
                "knowbase answered renditions outside user-data/v2",
                code="knowbase_contract_invalid",
            )
        return [r for r in body if isinstance(r, dict)]


def _refused(exc: KnowbaseRefused) -> NotFoundError | BadGatewayError:
    if exc.status == 404:
        return NotFoundError("No such material", code="material_not_found")
    return BadGatewayError(
        f"knowbase refused: {exc.code}"[:200], code="knowbase_refused"
    )
