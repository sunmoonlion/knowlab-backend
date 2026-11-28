"""Compare the pair fixture with the real stream adapter using the same cursor."""

import httpx
import pytest

from app.application.services.web_interaction import (
    REFERENCE_EVENT_IDS,
    REFERENCE_RUN_ID,
)
from scripts import pair_fixture


@pytest.mark.asyncio
async def test_resume_at_citation_delivers_only_new_events():
    cursor = str(REFERENCE_EVENT_IDS[2])
    adapter = pair_fixture._adapter
    stream = await adapter.open_run_stream(
        str(REFERENCE_RUN_ID), cursor, pair_fixture._web_context()
    )
    expected = [str(event.event_id) async for event in stream]
    assert expected == [str(REFERENCE_EVENT_IDS[3])]
    transport = httpx.ASGITransport(app=pair_fixture.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        client.cookies.set("sunmoonai_knowlab_web_sid", "e2e-session")
        response = await client.get(
            f"/api/web/v1/runs/{REFERENCE_RUN_ID}/events",
            headers={"Last-Event-ID": cursor},
        )
    assert response.status_code == 200
    assert [
        line[4:] for line in response.text.splitlines() if line.startswith("id: ")
    ] == expected
