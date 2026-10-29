"""按关系校验其他应用的服务令牌（做法同 knowbase、knowbench、knowmart、
knowfeed 的 relation_auth）。

knowlab 收一种关系：knowbench 替用户提孵化请求（incubation-request，
请专家方案设计第 4.9 节第 6
条、专家孵化方案第十三节）——用户在 knowbench 找不到合适的专家、点了「孵化一个专家」，
knowlab 以
这个用户的名义建一份种类为「问题」的申请。`SERVICE_AUTH_<关系>_CALLERS` 列出调用方，
每个调用方
一组 `SERVICE_AUTH_<关系>_<调用方>_AUDIENCE`（调用方的 client_id）、`_SUBJECT_ALLOWLIST`
（`admin/<调用方服务应用>`）、`_REQUIRED_SCOPE`（`knowlab:<关系>`），都由部署从调用方配置推出来
（`common/backend/service-client`）。关系名带连字符，环境变量里换成下划线。
只认 knowbench
（`RELATION_CALLERS`）。一组都没配就是这种关系没开，一律拒绝。签名、签发方、
有效期用模板的 OIDC
客户端核。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Header

from app.application.audit_context import set_actor
from app.application.errors.exceptions import (
    ForbiddenError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.domain.security import Principal
from app.infrastructure.security.oidc import OidcProviderClient
from core.config import Settings, get_settings

Relation = Literal["incubation-request"]
# 每种关系只认这些调用方
RELATION_CALLERS: dict[str, frozenset[str]] = {
    "incubation-request": frozenset({"knowbench"})
}


CALLER = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _key(name: str) -> str:
    return name.upper().replace("-", "_")


@dataclass(frozen=True)
class RelationBinding:
    relation: str
    caller: str
    audience: str
    subjects: frozenset[str]
    required_scope: str


@dataclass(frozen=True)
class ServiceCaller:
    """通过校验的调用方：哪个应用、以哪种关系。"""

    caller: str
    principal: Principal


def relation_bindings(
    relation: str, environ: Mapping[str, str] | None = None
) -> list[RelationBinding]:
    env = os.environ if environ is None else environ
    callers = [
        c.strip()
        for c in env.get(f"SERVICE_AUTH_{_key(relation)}_CALLERS", "").split(",")
    ]
    allowed = RELATION_CALLERS.get(relation, frozenset())
    bindings: list[RelationBinding] = []
    for caller in callers:
        if not CALLER.fullmatch(caller) or caller not in allowed:
            continue
        prefix = f"SERVICE_AUTH_{_key(relation)}_{_key(caller)}_"
        audience = env.get(prefix + "AUDIENCE", "").strip()
        subjects = frozenset(
            s.strip()
            for s in env.get(prefix + "SUBJECT_ALLOWLIST", "").split(",")
            if s.strip()
        )
        scope = env.get(prefix + "REQUIRED_SCOPE", "").strip()
        if audience and subjects and scope == f"knowlab:{relation}":
            bindings.append(
                RelationBinding(relation, caller, audience, subjects, scope)
            )
    return bindings


class RelationServiceVerifier:
    def __init__(
        self,
        relation: Relation,
        *,
        settings: Settings | None = None,
        oidc_client: OidcProviderClient | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._relation = relation
        self._settings = settings or get_settings()
        self._environ = environ
        self._oidc = oidc_client or OidcProviderClient(
            self._settings, self._settings.browser_profile("admin")
        )

    async def verify(self, encoded: str) -> ServiceCaller:
        bindings = relation_bindings(self._relation, self._environ)
        if not bindings:
            raise ServiceUnavailableError(
                "This service relation is not configured",
                code="service_relation_unconfigured",
            )
        if not encoded or len(encoded) > 16384:
            raise UnauthorizedError(
                "The service identity token is invalid", code="token_invalid"
            )
        # 受众就是调用方的 client_id：逐个调用方试，签名、签发方、有效期每次都核
        claims: dict[str, Any] | None = None
        binding = bindings[0]
        for candidate in bindings:
            try:
                claims = await self._oidc.verify_access_token(
                    encoded, audience=candidate.audience
                )
            except UnauthorizedError as exc:
                if exc.code == "audience_mismatch":
                    continue
                raise
            binding = candidate
            break
        if claims is None:
            raise UnauthorizedError(
                "The service identity audience is invalid", code="audience_mismatch"
            )
        subject = claims.get("sub")
        issuer = claims.get("iss")
        if not isinstance(subject, str) or not isinstance(issuer, str) or not issuer:
            raise UnauthorizedError(
                "The service identity token is invalid", code="token_invalid"
            )
        if subject not in binding.subjects:
            raise ForbiddenError(
                "The service identity is not bound to this relation",
                code="service_subject_unbound",
            )
        if binding.required_scope not in _scopes(claims):
            raise ForbiddenError(
                "The service identity scope is not authorized",
                code="service_scope_denied",
            )
        return ServiceCaller(
            caller=binding.caller,
            principal=Principal(
                actor_type="service",
                subject=subject,
                issuer=issuer,
                app=self._settings.app_slug,
                surface="internal",
                audience=binding.audience,
                scopes=frozenset({binding.required_scope}),
                authenticated_at=_timestamp(claims.get("iat")),
                expires_at=_timestamp(claims.get("exp")),
                policy_version="service-v1",
            ),
        )


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, (int, float)):
        raise UnauthorizedError(
            "The service identity token is invalid", code="token_invalid"
        )
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise UnauthorizedError(
            "The service identity token is invalid", code="token_invalid"
        ) from exc


def _scopes(claims: dict[str, Any]) -> frozenset[str]:
    raw = claims.get("scope", claims.get("scp", ()))
    if isinstance(raw, str):
        return frozenset(re.split(r"\s+", raw.strip())) if raw.strip() else frozenset()
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return frozenset(raw)
    raise UnauthorizedError(
        "The service identity token has invalid scopes", code="token_invalid"
    )


def _bearer(authorization: str | None) -> str:
    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not encoded or " " in encoded:
        raise UnauthorizedError(
            "A Bearer service identity is required", code="service_auth_required"
        )
    return encoded


_verifiers: dict[str, RelationServiceVerifier] = {}


def relation_verifier(relation: Relation) -> RelationServiceVerifier:
    if relation not in _verifiers:
        _verifiers[relation] = RelationServiceVerifier(relation)
    return _verifiers[relation]


async def _verify(relation: Relation, authorization: str | None) -> ServiceCaller:
    caller = await relation_verifier(relation).verify(_bearer(authorization))
    set_actor(caller.principal.subject)
    return caller


async def require_incubation_request_service(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ServiceCaller:
    """knowbench 替用户提孵化请求（请专家方案设计第 4.9 节第 6 条）。"""
    return await _verify("incubation-request", authorization)
