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

from app.application.errors.exceptions import BadGatewayError, NotFoundError
from app.infrastructure.external.knowbase_client import (
    KnowbaseClient,
    KnowbaseRefused,
    _segment,
)

COLUMN = "knowlab"


class KnowbaseLibrary(KnowbaseClient):
    BASE = "/api/internal/v2/user-data"

    async def _get(self, path: str, user_token: str) -> Any:
        try:
            _, body, _ = await self._send("GET", path, user_token)
        except KnowbaseRefused as exc:
            if exc.status == 404:
                raise NotFoundError(
                    "No such material", code="material_not_found"
                ) from None
            raise BadGatewayError(
                f"knowbase refused: {exc.code}"[:200], code="knowbase_refused"
            ) from None
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
