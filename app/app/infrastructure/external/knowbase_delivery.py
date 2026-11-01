"""knowbase 的交付、测试证据与名录（契约 user-data/v2；SDD 0043 第 2.6、4.2、4.4 节，
0044 第三节，0046 第五节）。

- 服务 `user-data-directory`：推名下专家的名录（`PUT /experts/{专家}`）、交付时冻结
  绑定（`POST /experts/{专家}/versions/{版本}/freeze`，每份带钉住那一版的摘要与当时的
  许可）、交审前预查所绑数据的许可（`POST /service/expert-bindings/check`）；
- 服务 `user-data-grant-test`：平台重跑的机器授权（`POST /service/run-grants`，过程
  `platform`、读的一方 `machine`）、审完撤销平台过程（`POST /service/platform-processes/
  {种类}/{编号}/end`）；
- 服务 `user-data-output`：测试证据写进作者的产出区（`POST /outputs`，请求体就是文件）；
- 作者本人令牌：交审时留下平台过程的依据（`POST /platform-consents`）。

令牌只在内存里，不进日志。knowbase 拒了抛 BadGatewayError（code 照 knowbase 给的），
不可达抛 ServiceUnavailableError。
"""

from __future__ import annotations

from typing import Any

from app.application.errors.exceptions import BadGatewayError
from app.infrastructure.external.knowbase_client import (
    KnowbaseClient,
    KnowbaseRefused,
    _segment,
)


class KnowbaseDelivery(KnowbaseClient):
    BASE = "/api/internal/v2/user-data"

    async def _send(self, *args: Any, **kwargs: Any) -> Any:
        """拒了的照 knowbase 给的 code 抛 BadGatewayError（应用层不认这里的异常）。"""
        try:
            return await super()._send(*args, **kwargs)
        except KnowbaseRefused as exc:
            raise BadGatewayError(
                f"knowbase refused: {exc.code}"[:200], code=exc.code[:64]
            ) from None

    # —— 名录与绑定（user-data-directory）——

    async def push_expert(
        self, expert_id: str, *, owner: str, name: str, latest_version: str | None
    ) -> None:
        token = await self._service_token("user-data-directory")
        await self._send(
            "PUT",
            f"/experts/{_segment(expert_id)}",
            token,
            json={"owner": owner, "name": name[:200], "latest_version": latest_version},
        )

    async def freeze(
        self, expert_id: str, version: str, *, author: str, preview: bool = False
    ) -> list[dict[str, Any]]:
        """冻结只一次：同一版再调回第一次冻结的那几条。preview 只回「现在冻结会冻成
        什么」、不写（提交前预查许可，预查不过不冻结）。"""
        token = await self._service_token("user-data-directory")
        _, body, _ = await self._send(
            "POST",
            f"/experts/{_segment(expert_id)}/versions/{_segment(version)}/freeze",
            token,
            json={"author": author, "preview": preview},
        )
        if not isinstance(body, list):
            raise BadGatewayError(
                "knowbase answered the freeze outside user-data/v2",
                code="knowbase_contract_invalid",
            )
        return [b for b in body if isinstance(b, dict)]

    async def void_frozen(self, expert_id: str, version: str, *, author: str) -> int:
        """作废没交成的那次冻结（作者撤回、平台作废没交到 knowmart 的交付）。"""
        token = await self._service_token("user-data-directory")
        _, body, _ = await self._send(
            "POST",
            f"/experts/{_segment(expert_id)}/versions/{_segment(version)}/freeze/void",
            token,
            json={"author": author},
        )
        return int((body or {}).get("withdrawn") or 0)

    async def raise_frozen_license(
        self,
        expert_id: str,
        version: str,
        material_id: str,
        *,
        author: str,
        license_: str,
    ) -> dict[str, Any]:
        """自用转公开时把冻结的那一份许可往高改；作者在提交时勾了「我有权」。"""
        token = await self._service_token("user-data-directory")
        _, body, _ = await self._send(
            "POST",
            f"/experts/{_segment(expert_id)}/versions/{_segment(version)}"
            f"/frozen/{_segment(material_id)}/license",
            token,
            json={"author": author, "license": license_, "attest": True},
        )
        return dict(body or {})

    async def binding_states(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        token = await self._service_token("user-data-directory")
        _, body, _ = await self._send(
            "POST", "/service/expert-bindings/check", token, json={"items": items}
        )
        rows = body.get("items") if isinstance(body, dict) else None
        if not isinstance(rows, list) or len(rows) != len(items):
            raise BadGatewayError(
                "knowbase answered the binding check outside user-data/v2",
                code="knowbase_contract_invalid",
            )
        return [r if isinstance(r, dict) else {} for r in rows]

    # —— 平台过程（本人令牌留依据；user-data-grant-test 签机器授权、撤）——

    async def consent_platform(
        self, user_token: str, *, kind: str, ref: str, items: list[dict[str, Any]]
    ) -> int:
        _, body, _ = await self._send(
            "POST",
            "/platform-consents",
            user_token,
            json={"kind": kind, "ref": ref, "items": items},
        )
        return len(body) if isinstance(body, list) else 0

    async def end_platform(
        self, *, subject: str, kind: str, ref: str, reason: str
    ) -> None:
        token = await self._service_token("user-data-grant-test")
        await self._send(
            "POST",
            f"/service/platform-processes/{_segment(kind)}/{_segment(ref)}/end",
            token,
            json={"subject": subject, "reason": reason},
        )

    async def platform_consent_live(self, *, subject: str, kind: str, ref: str) -> int:
        """本人给这次平台过程留的依据还有几份有效（只读；作者在 knowbase 总栏撤了就是
        0）。knowbase 按架构不调别的应用，由 knowlab 后台来查。"""
        token = await self._service_token("user-data-grant-test")
        _, body, _ = await self._send(
            "GET",
            f"/service/platform-processes/{_segment(kind)}/{_segment(ref)}",
            token,
            params={"subject": subject},
        )
        if not isinstance(body, dict) or not isinstance(body.get("live"), int):
            raise BadGatewayError(
                "knowbase answered the process outside user-data/v2",
                code="knowbase_contract_invalid",
            )
        return int(body["live"])

    async def machine_grant(
        self,
        *,
        owner: str,
        ref: str,
        items: list[dict[str, Any]],
        ttl_seconds: int,
        platform_use: str,
    ) -> str:
        """平台重跑这一条用例要读的：读的一方是作者的电脑（运行方 knowbench）。过程
        照 platform_use 记：一条用例可能全是公开原件、引用、冻结绑定，没有凭平台同意
        的项。"""
        token = await self._service_token("user-data-grant-test")
        _, body, _ = await self._send(
            "POST",
            "/service/run-grants",
            token,
            json={
                "process": "platform",
                "beneficiary": owner,
                "ref": ref,
                "items": items,
                "reader_role": "machine",
                "ttl_seconds": ttl_seconds,
                "platform_use": platform_use,
            },
        )
        return str(body["grant_id"])

    async def revoke_grant(self, grant_id: str) -> None:
        token = await self._service_token("user-data-grant-test")
        await self._send("DELETE", f"/service/run-grants/{_segment(grant_id)}", token)

    # —— 产出区（user-data-output）——

    async def write_output(
        self,
        *,
        beneficiary: str,
        task_ref: str,
        run_ref: str,
        name: str,
        content: bytes,
        expert_id: str,
        expert_version: str,
    ) -> dict[str, Any]:
        token = await self._service_token("user-data-output")
        _, body, _ = await self._send(
            "POST",
            "/outputs",
            token,
            params={
                "beneficiary": beneficiary,
                "task_ref": task_ref,
                "run_ref": run_ref,
                "name": name,
                "evidence": "true",
                "expert_id": expert_id,
                "expert_version": expert_version,
            },
            content=content,
        )
        if not isinstance(body, dict) or not body.get("material_id"):
            raise BadGatewayError(
                "knowbase answered the output outside user-data/v2",
                code="knowbase_contract_invalid",
            )
        return {
            "material_id": str(body["material_id"]),
            "version_no": int(body.get("version_no") or 1),
        }
