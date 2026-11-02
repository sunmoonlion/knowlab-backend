"""Reference-aware compilation and cross-app schema locks."""

import hashlib
import json
from pathlib import Path

import jsonschema

from app.domain.knowbots.compile import compile_manifest, decompile, pack_problems
from tests.referenced_method_fixtures import build

PARENT = Path(__file__).resolve().parents[3]


def test_compiler_preserves_independent_reference_and_import_roundtrip():
    manifest, errors = build()
    assert not errors and not pack_problems(manifest["pack"])
    body, notes = decompile(manifest)
    assert not notes and body.method_refs["report"].version == 2
    rebuilt, errors = compile_manifest(
        body,
        {},
        pack_id=manifest["pack"]["pack_id"],
        version="1",
        runtime="knowbench",
        fallback_name="synthetic",
        method_sources=manifest["pack"]["method_sources"],
    )
    assert not errors and rebuilt["pack"] == manifest["pack"]
    _, missing = compile_manifest(
        body,
        {},
        pack_id="missing",
        version="1",
        runtime="knowbench",
        fallback_name="missing",
    )
    assert missing  # no silently downgraded reference-free expert


def test_reference_compilation_matches_consumer_fixture_and_provider_schema():
    manifest, errors = build()
    assert not errors
    lock = json.loads(
        (PARENT / "contracts/knowbench-dynamic-methods-v2-lock.json").read_text()
    )
    schema = PARENT.parent / lock["provider"] / lock["schema"]
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == lock["sha256"]
    jsonschema.validate(manifest["pack"], json.loads(schema.read_text()))
    fixture = (
        PARENT.parent
        / "knowbench-app/knowbench-backend/app/tests/fixtures"
        / "referenced-methods-manifest.json"
    )
    assert json.loads(fixture.read_text()) == manifest
