"""Retired author method-version routes are absent for signed-in owners."""

from uuid import uuid4

import pytest

from tests.test_knowbot_workdraft_routes import WEB, WEB_SID, client
from tests.test_knowbot_workdraft_routes import wired as wired


@pytest.mark.asyncio
async def test_retired_version_cannot_be_read_or_overwritten(wired):
    url = f"/api/web/v1/method-units/{uuid4()}/versions/1"
    async with client() as browser:
        browser.cookies.set(WEB_SID, "member")
        assert (await browser.get(url)).status_code == 404
        assert (await browser.put(url, json={}, headers=WEB)).status_code == 404
