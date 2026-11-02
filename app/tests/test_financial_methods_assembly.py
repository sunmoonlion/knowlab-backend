"""Test Assembly of Complete 20 Financial & Valuation Methods into Financial Expert Manifest.
Verifies:
- All 20 author methods freeze with valid provider schema and independent slot names.
- Complete financial & valuation expert assembly compiles cleanly with 0 errors.
- Compiled manifest strictly adheres to knowbench/dynamic-methods/v2 contract.
- Exports golden fixture for Luna's knowbench execution integration.
"""

import json
from pathlib import Path
import jsonschema
import pytest

from app.domain.knowbots.methods import render
from tests.financial_author_method_fixtures import (
    ALL_20_SPECS,
    BATCH1_SPECS,
    BATCH2_SPECS,
    BATCH3_SPECS,
    build_financial_expert_manifest,
    make_author_method_definition,
)

PARENT = Path(__file__).resolve().parents[3]


def test_all_20_author_methods_validity():
    assert len(ALL_20_SPECS) == 20
    assert len(BATCH1_SPECS) == 8
    assert len(BATCH2_SPECS) == 6
    assert len(BATCH3_SPECS) == 6
    
    validator = jsonschema.Draft202012Validator(json.loads(render()))
    
    digests = set()
    for spec in ALL_20_SPECS:
        defn = make_author_method_definition(spec)
        assert defn.step.id == "method"
        assert defn.step.selection.required is False
        assert len(defn.step.uses) > 0
        assert set(defn.step.uses) == set(defn.input_schemas.keys())
        
        # Validate against official provider schema
        validator.validate(defn.document())
        
        # Digest check
        digest = defn.digest()
        assert digest.startswith("sha256:")
        assert digest not in digests
        digests.add(digest)


def test_financial_expert_manifest_compilation_and_export():
    manifest, errors = build_financial_expert_manifest()
    assert not errors, f"Expected 0 compiler errors, got: {errors}"
    
    pack = manifest["pack"]
    assert pack["runtime_contract"] == "knowbench/dynamic-methods/v2"
    assert pack["method_compiler"] == "knowlab/workdraft-compiler/v1"
    
    # All 20 methods bound
    assert len(pack["method_sources"]) == 20
    for spec in ALL_20_SPECS:
        m_id = spec["id"]
        assert m_id in pack["method_sources"]
        source = pack["method_sources"][m_id]
        assert "reference" in source
        assert "definition" in source
        assert source["reference"]["version"] == 1
        assert source["reference"]["digest"].startswith("sha256:")
    
    # Validate pack against knowbench-dynamic-methods-v2 schema lock
    lock = json.loads(
        (PARENT / "contracts/knowbench-dynamic-methods-v2-lock.json").read_text()
    )
    schema_path = PARENT.parent / lock["provider"] / lock["schema"]
    jsonschema.validate(pack, json.loads(schema_path.read_text()))
    
    # Export manifest to knowlab-backend fixtures for Luna
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    manifest_out = fixtures_dir / "financial-expert-manifest.json"
    manifest_out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    assert manifest_out.exists()
