"""knowlab 交付给 knowmart 的清单：锁与提供方 schema 一致，样例清单合提供方 schema。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.domain.knowbots.data_request import DataRequest
from app.domain.knowbots.drafts import draft_manifest_problems
from tests.knowbot_fakes import manifest, manifest_v2

PARENT = Path(__file__).resolve().parents[3]
LOCK = PARENT / "contracts/knowmart-knowbot-manifest-lock.json"


def _lock() -> dict:
    if not LOCK.exists():  # 只拿到后端子仓时没有父仓
        pytest.skip("parent contracts/ is not checked out")
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _provider_schema(lock: dict) -> Path:
    configured = os.environ.get("KNOWMART_KNOWBOT_MANIFEST_SCHEMA_PATH")
    path = (
        Path(configured)
        if configured
        else PARENT.parent / "knowmart-app" / lock["schema"]
    )
    if not path.exists():
        pytest.skip("knowmart-app is not checked out next to knowlab-app")
    return path


def test_lock_matches_the_provider_schema() -> None:
    lock = _lock()
    assert lock["provider"] == "knowmart-app"
    assert lock["contract"] == "knowbot-manifest" and lock["major_version"] == 1
    schema = _provider_schema(lock)
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == lock["sha256"]


def test_a_manifest_knowlab_accepts_satisfies_the_provider_schema() -> None:
    schema = _provider_schema(_lock())
    jsonschema = pytest.importorskip("jsonschema")
    doc = manifest()
    assert draft_manifest_problems(doc, runtime="knowbench", version="1") == []
    jsonschema.validate(doc, json.loads(schema.read_text(encoding="utf-8")))


# —— 清单 v2 与一样数据的描述（SDD 0043 块 4、6a）——

LOCK_V2 = PARENT / "contracts/knowmart-knowbot-manifest-v2-lock.json"
LOCK_REQUEST = PARENT / "contracts/knowbase-data-request-lock.json"


def _read_lock(path: Path) -> dict:
    if not path.exists():
        pytest.skip("parent contracts/ is not checked out")
    return json.loads(path.read_text(encoding="utf-8"))


def _sibling(app: str, lock: dict) -> Path:
    path = PARENT.parent / app / lock["schema"]
    if not path.exists():
        pytest.skip(f"{app} is not checked out next to knowlab-app")
    return path


def _structure(node):
    if isinstance(node, dict):
        return {k: _structure(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_structure(v) for v in node]
    return node


def test_v2_locks_match_their_providers() -> None:
    v2 = _read_lock(LOCK_V2)
    assert (v2["provider"], v2["major_version"]) == ("knowmart-app", 2)
    schema = _sibling("knowmart-app", v2)
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == v2["sha256"]
    request = _read_lock(LOCK_REQUEST)
    assert (request["provider"], request["contract"]) == (
        "knowbase-app",
        "data-request",
    )
    provider = _sibling("knowbase-app", request)
    assert hashlib.sha256(provider.read_bytes()).hexdigest() == request["sha256"]


def test_our_data_request_is_the_knowbase_data_request() -> None:
    provider = json.loads(
        _sibling("knowbase-app", _read_lock(LOCK_REQUEST)).read_text(encoding="utf-8")
    )
    ours = DataRequest.model_json_schema(ref_template="#/$defs/{model}")
    defs = ours.pop("$defs", {})
    defs["DataRequest"] = ours
    assert _structure(defs) == _structure(provider["$defs"])


def test_a_v2_draft_knowlab_accepts_satisfies_the_knowmart_v2_schema() -> None:
    schema = _sibling("knowmart-app", _read_lock(LOCK_V2))
    jsonschema = pytest.importorskip("jsonschema")
    doc = manifest_v2()
    assert draft_manifest_problems(doc, runtime="knowbench", version="1") == []
    jsonschema.validate(doc, json.loads(schema.read_text(encoding="utf-8")))
