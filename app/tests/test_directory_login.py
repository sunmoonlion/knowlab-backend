"""Complete local login with the return path supplied by the method-directory page."""

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_auth_service import (
    FakeRedis,
    FakeRedisHolder,
    StubAuthService,
    build,
    session_store_module,
    settings,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("locale", ["en", "zh-CN"])
@pytest.mark.parametrize("page", ["methods", "studio", "studio/123?tab=contract"])
async def test_login_returns_to_requested_page(monkeypatch, locale, page):
    redis = FakeRedis()
    monkeypatch.setattr(
        session_store_module, "get_redis", lambda: FakeRedisHolder(redis)
    )
    service = build(StubAuthService, "web", settings())
    requested = f"/{locale}/{page}"
    start = await service.begin_login(requested)
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    _, actual = await service.complete_login(
        code="local", state=state, transaction_id=start.transaction_id
    )
    assert actual == requested
