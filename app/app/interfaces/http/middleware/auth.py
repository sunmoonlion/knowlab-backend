from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request

from app.application.audit_context import set_actor
from app.application.errors.exceptions import (
    ForbiddenError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.application.services.auth_service import AuthService
from app.bootstrap.auth import build_auth_service
from app.domain.security import BrowserSession, Principal
from app.infrastructure.security import ServiceIdentityVerifier
from core.config import get_settings

_settings = get_settings()
_admin_profile = _settings.browser_profile("admin")
_web_profile = _settings.browser_profile("web")
admin_auth_service = build_auth_service("admin", _settings)
web_auth_service = build_auth_service("web", _settings)
service_identity_verifier = ServiceIdentityVerifier(_settings)


async def _session(
    *,
    service: AuthService,
    request: Request,
    session_id: str | None,
    csrf_token: str | None,
) -> BrowserSession:
    session = await service.get_browser_session(session_id)
    if session is None or session.principal.actor_type != "user":
        raise UnauthorizedError()
    service.validate_csrf(
        session=session,
        method=request.method,
        origin=request.headers.get("origin"),
        csrf_token=csrf_token,
    )
    set_actor(str(session.principal.actor_id))
    return session


async def get_admin_browser_session(
    request: Request,
    session_id: str | None = Cookie(
        default=None, alias=_admin_profile.session_cookie_name
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> BrowserSession:
    return await _session(
        service=admin_auth_service,
        request=request,
        session_id=session_id,
        csrf_token=csrf_token,
    )


async def get_web_browser_session(
    request: Request,
    session_id: str | None = Cookie(
        default=None, alias=_web_profile.session_cookie_name
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> BrowserSession:
    return await _session(
        service=web_auth_service,
        request=request,
        session_id=session_id,
        csrf_token=csrf_token,
    )


async def get_web_knowbase_token(
    session: Annotated[BrowserSession, Depends(get_web_browser_session)],
    session_id: str | None = Cookie(
        default=None, alias=_web_profile.session_cookie_name
    ),
) -> str:
    """调 knowbase 用户资料用的用户令牌（SDD 0042）：这个会话的访问令牌换来的。

    先过会话与 CSRF 的检查（依赖 get_web_browser_session），再换；没配 knowbase 就 503。
    """
    del session
    audience = get_settings().knowbase_user_audience.strip()
    if not audience:
        raise ServiceUnavailableError(
            "knowbase is not configured", code="knowbase_unconfigured"
        )
    return await web_auth_service.user_token(session_id, audience=audience)


async def get_web_knowbase_token_provider(
    session: Annotated[BrowserSession, Depends(get_web_browser_session)],
    session_cookie: str | None = Cookie(
        default=None, alias=_web_profile.session_cookie_name
    ),
) -> Callable[[], Awaitable[str]]:
    """同 get_web_knowbase_token，但真要用时才换（知识库页只在要动 knowbase 时才要）。

    参数不叫 session_id：挂在带 {session_id} 的路径上时会和路径参数撞名。
    """
    del session

    async def provide() -> str:
        audience = get_settings().knowbase_user_audience.strip()
        if not audience:
            raise ServiceUnavailableError(
                "knowbase is not configured", code="knowbase_unconfigured"
            )
        return await web_auth_service.user_token(session_cookie, audience=audience)

    return provide


async def get_admin_current_user(
    session: Annotated[BrowserSession, Depends(get_admin_browser_session)],
) -> Principal:
    return session.principal


async def get_web_current_user(
    session: Annotated[BrowserSession, Depends(get_web_browser_session)],
) -> Principal:
    return session.principal


# 部署只给角色、不发权限（账 134），所以管理角色换算成权限（待办 98，
# 所有者 2026-10-18 采纳审读第 4 条）：admin 什么都能做；白名单里别的角色
# （operator）拿管理端的基本权限，交付、退回这类接口在基本权限之外再要一项。
# 所有者 2026-10-23 改定：角色仍分 admin、operator，但现在 operator 也给这一项，
# 管理端的事两种角色都能做；admin 比 operator 多哪些权限以后定，定了就从这里拿掉。
FULL_ADMIN_ROLE = "admin"
OPERATOR_SCOPES = frozenset({"knowlab:release"})


def granted_scopes(principal: Principal) -> frozenset[str] | None:
    """这个人在管理端有哪些权限；None 是全部。"""
    roles = set(principal.roles) & _admin_profile.role_allowlist
    if FULL_ADMIN_ROLE in roles:
        return None
    if not roles:
        return frozenset(principal.scopes)
    base = frozenset(_admin_profile.required_scopes) | OPERATOR_SCOPES
    return frozenset(principal.scopes) | base


def require_admin_scopes(*required: str) -> Callable[..., Awaitable[Principal]]:
    required_set = frozenset(required)

    async def dependency(
        session: Annotated[BrowserSession, Depends(get_admin_browser_session)],
    ) -> Principal:
        principal = session.principal
        granted = granted_scopes(principal)
        if granted is not None and not required_set <= granted:
            raise ForbiddenError("Required scope missing")
        return principal

    return dependency


require_knowlab_admin = require_admin_scopes(*_admin_profile.required_scopes)
# 建草稿、核出处、交付、退回：多要一项 knowlab:release（2026-10-18 审读第 4 条）；
# 现在 operator 也有这一项（所有者 2026-10-23，见 OPERATOR_SCOPES）
require_knowlab_release = require_admin_scopes(
    *_admin_profile.required_scopes, "knowlab:release"
)


async def get_internal_service_principal(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Principal:
    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not encoded or " " in encoded:
        raise UnauthorizedError(
            "A Bearer service identity is required", code="service_auth_required"
        )
    principal = await service_identity_verifier.verify(
        encoded, required_scopes=frozenset()
    )
    set_actor(principal.subject)
    return principal


def require_internal_scopes(*required: str) -> Callable[..., Awaitable[Principal]]:
    required_set = frozenset(required)

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Principal:
        scheme, _, encoded = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not encoded or " " in encoded:
            raise UnauthorizedError(
                "A Bearer service identity is required",
                code="service_auth_required",
            )
        principal = await service_identity_verifier.verify(
            encoded, required_scopes=required_set
        )
        set_actor(principal.subject)
        return principal

    return dependency
