"""knowlab 交付给 knowmart 的清单（v2）与一样数据的描述：锁与提供方 schema 一致，样例
清单合提供方 schema。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from app.domain.knowbots.data_request import DataRequest
from app.domain.knowbots.delivery import CATEGORY_CODES
from app.domain.knowbots.drafts import draft_manifest_problems
from app.domain.knowbots.supply_status import DemandStatusEventV1
from tests.knowbot_fakes import manifest

PARENT = Path(__file__).resolve().parents[3]


# —— 清单 v2 与一样数据的描述（SDD 0043 块 4、6a；decisions ⑰ 起清单只有 v2）——

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
    doc = manifest()
    assert draft_manifest_problems(doc, runtime="knowbench", version="1") == []
    jsonschema.validate(doc, json.loads(schema.read_text(encoding="utf-8")))


# —— 交付请求 knowbot-delivery/v1（带类目，knowmart 0032 待定第一项）——

LOCK_DELIVERY = PARENT / "contracts/knowmart-delivery-v1-lock.json"


def test_delivery_lock_matches_knowmart_and_our_categories() -> None:
    lock = _read_lock(LOCK_DELIVERY)
    assert (lock["provider"], lock["contract"], lock["major_version"]) == (
        "knowmart-app",
        "knowbot-delivery",
        1,
    )
    schema = _sibling("knowmart-app", lock)
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == lock["sha256"]
    provider = json.loads(schema.read_text(encoding="utf-8"))
    [codes] = [
        a["enum"] for a in provider["properties"]["category"]["anyOf"] if "enum" in a
    ]
    assert tuple(codes) == CATEGORY_CODES
    # 我们交过去的几样都在契约里，必填的都给
    sent = {
        "delivery_id",
        "owner_subject",
        "knowbot_ref",
        "manifest",
        "request_public",
        "category",
    }
    assert sent == set(provider["properties"])
    assert set(provider["required"]) <= sent


# —— knowfeed 推需求状态 knowfeed-supply-status/v1（SDD 0045 第 2.5 节）——

LOCK_SUPPLY_STATUS = PARENT / "contracts/knowfeed-supply-status-lock.json"


def test_supply_status_lock_matches_knowfeed_and_our_receiver() -> None:
    lock = _read_lock(LOCK_SUPPLY_STATUS)
    assert (lock["provider"], lock["contract"]) == (
        "knowfeed-app",
        "knowfeed-supply-status/v1",
    )
    schema = _sibling("knowfeed-app", lock)
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == lock["sha256"]
    provider = json.loads(schema.read_text(encoding="utf-8"))
    ours = DemandStatusEventV1.model_json_schema(ref_template="#/$defs/{model}")
    defs = ours.pop("$defs", {})
    for key in ("$schema", "$id", "title"):
        provider.pop(key, None)
        ours.pop(key, None)
    assert _structure(defs) == _structure(provider.pop("$defs"))
    assert _structure(ours) == _structure(provider)
    release = json.loads(
        (schema.parent / "contract-manifest.json").read_text(encoding="utf-8")
    )
    assert release["receiver_path"] == "/api/internal/v1/knowfeed/demand-status"
    assert release["relation"] == "supply-status"
