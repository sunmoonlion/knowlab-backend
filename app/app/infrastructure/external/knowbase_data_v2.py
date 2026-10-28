"""knowbase 的数据第四版（契约 user-data/v2，SDD 0043）：测试集要用的几样。

用于 SDD 0046 第三节：
- 用户本人（作者，令牌的 `azp` 是 knowlab）：按专家提出的几样在 `app:knowlab` 的
  范围里解析、确认前改清单、看清单、确认一次（要补采的）；
- 服务：重解析（`user-data-read`，补采办完后）、签与撤这条用例的测试运行授权
  （`user-data-grant-test`，凭展示依据、引用、公开原件、冻结绑定）。
令牌只在内存里，不进日志；拒了抛 KnowbaseRefused，不可达抛 ServiceUnavailableError。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.infrastructure.external.knowbase_client import KnowbaseClient, _segment

CONTRACT = "data-request/v1"


class KnowbaseDataV2(KnowbaseClient):
    BASE = "/api/internal/v2/user-data"

    # —— 作者本人 ——

    async def resolve(
        self,
        user_token: str,
        *,
        consumer: str,
        needs: list[dict[str, Any]],
        task_ref: str,
    ) -> dict[str, Any]:
        _, body, _ = await self._send(
            "POST",
            "/resolutions",
            user_token,
            json={
                "consumer": consumer,
                "contract_version": CONTRACT,
                "needs": needs,
                "params": {"task_date": datetime.now(UTC).date().isoformat()},
                "task_ref": task_ref,
            },
        )
        return body

    async def resolution(self, user_token: str, resolution_id: str) -> dict[str, Any]:
        _, body, _ = await self._send(
            "GET", f"/resolutions/{_segment(resolution_id)}", user_token
        )
        return body

    async def revise_needs(
        self, user_token: str, resolution_id: str, needs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        _, body, _ = await self._send(
            "PUT",
            f"/resolutions/{_segment(resolution_id)}/needs",
            user_token,
            json={"needs": needs},
        )
        return body

    async def confirm(
        self,
        user_token: str,
        *,
        items: list[dict[str, Any]],
        task_ref: str,
        resolution_id: str,
        revision: int,
    ) -> dict[str, Any]:
        _, body, _ = await self._send(
            "POST",
            "/confirmations",
            user_token,
            json={
                "kind": "description",
                "items": items,
                "supply": {},
                "task_ref": task_ref,
                "resolution_id": resolution_id,
                "revision": revision,
            },
        )
        return body

    # —— 服务 ——

    async def refresh(self, subject: str, resolution_id: str) -> dict[str, Any]:
        token = await self._service_token("user-data-read")
        _, body, _ = await self._send(
            "POST",
            f"/service/resolutions/{_segment(resolution_id)}/refresh",
            token,
            params={"subject": subject},
        )
        return body

    async def case_grant(
        self,
        *,
        owner: str,
        ref: str,
        items: list[dict[str, Any]],
        ttl_seconds: int,
    ) -> str:
        """这条用例的测试运行授权（读的一方是运行方 knowbench，不中途追加）。"""
        token = await self._service_token("user-data-grant-test")
        _, body, _ = await self._send(
            "POST",
            "/service/run-grants",
            token,
            json={
                "process": "test",
                "beneficiary": owner,
                "ref": ref,
                "items": items,
                "ttl_seconds": ttl_seconds,
            },
        )
        return str(body["grant_id"])

    async def revoke_case_grant(self, grant_id: str) -> None:
        token = await self._service_token("user-data-grant-test")
        await self._send("DELETE", f"/service/run-grants/{_segment(grant_id)}", token)
