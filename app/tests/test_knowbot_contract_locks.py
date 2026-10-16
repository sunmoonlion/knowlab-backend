"""knowlab 交付给 knowmart 的清单：锁与提供方 schema 一致，样例清单合提供方 schema。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.domain.knowbots.drafts import draft_manifest_problems
from tests.knowbot_fakes import manifest

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
    assert draft_manifest_problems(doc, runtime="investment", version="1") == []
    jsonschema.validate(doc, json.loads(schema.read_text(encoding="utf-8")))
