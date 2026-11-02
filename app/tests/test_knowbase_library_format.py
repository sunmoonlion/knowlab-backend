import hashlib
import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from app.application.errors.exceptions import BadGatewayError, NotFoundError
from app.infrastructure.external.knowbase_library import KnowbaseLibrary
from tests.test_knowbase_client import environ

BODY = {
    "material_id": "m1",
    "version_no": 2,
    "sha256": "a" * 64,
    "media_type": "text/csv",
    "tables": [{"name": "", "columns": ["metric", "2024"], "rows": ["revenue"]}],
}
ROOT = Path(__file__).resolve().parents[3]


def test_format_sample_validates_against_pinned_provider_contract():
    lock = json.loads((ROOT / "contracts/knowbase-user-data-lock.json").read_text())
    path = ROOT.parent / lock["provider"] / lock["schema"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == lock["sha256"]
    schema = json.loads(path.read_text())
    jsonschema.validate(BODY, schema | {"$ref": "#/$defs/MaterialFormatRead"})


async def test_format_client_uses_exact_version_user_token_and_allows_new_fields():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=BODY
            | {"future": True, "tables": [BODY["tables"][0] | {"future": True}]},
        )

    client = KnowbaseLibrary(environ(), transport=httpx.MockTransport(respond))
    assert await client.read_format("user-token", "m1", 2) == BODY
    assert seen[0].url.path.endswith("/materials/m1/versions/2/format")
    assert seen[0].headers["authorization"] == "Bearer user-token"


@pytest.mark.parametrize(
    "body",
    [{}, BODY | {"tables": [{"columns": ["x" * 201]}]}, BODY | {"version_no": True}],
)
async def test_malformed_response_does_not_become_empty_format(body):
    client = KnowbaseLibrary(
        environ(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    with pytest.raises(BadGatewayError) as exc:
        await client.read_format("user-token", "m1", 2)
    assert exc.value.code == "knowbase_contract_invalid"


async def test_hidden_or_revoked_material_stays_not_found():
    client = KnowbaseLibrary(
        environ(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(404, json={"code": "item_not_found"})
        ),
    )
    with pytest.raises(NotFoundError):
        await client.read_format("user-token", "m1", 2)
