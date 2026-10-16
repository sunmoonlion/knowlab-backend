"""父仓 contracts/knowbot-test-run/v1 必须就是从模型生成的那一份，摘要对得上。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.domain.knowbots.schema import DOCUMENTS, render
from app.domain.knowbots.test_run import CONTRACT
from tests.knowbot_fakes import manifest, request_body, result

CONTRACT_DIR = Path(__file__).resolve().parents[3] / "contracts/knowbot-test-run/v1"
REGENERATE = "regenerate: python -m app.domain.knowbots.schema"


def _require_contract() -> None:
    if not CONTRACT_DIR.exists():  # 只拿到后端子仓时（例如镜像构建上下文）没有父仓
        pytest.skip("parent contracts/ is not checked out")


def _schema(name: str) -> dict:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_schema_files_are_generated_from_the_models(name: str) -> None:
    _require_contract()
    assert (CONTRACT_DIR / name).read_text(encoding="utf-8") == render(name), REGENERATE


def test_contract_manifest_digests_match_the_schemas() -> None:
    _require_contract()
    release = json.loads((CONTRACT_DIR / "contract-manifest.json").read_text())
    assert release["contract"] == "knowbot-test-run"
    assert release["major_version"] == 1
    assert set(release["schemas"]) == set(DOCUMENTS)
    for name, digest in release["schemas"].items():
        assert digest == hashlib.sha256((CONTRACT_DIR / name).read_bytes()).hexdigest()


def test_examples_satisfy_the_published_schemas() -> None:
    _require_contract()
    jsonschema = pytest.importorskip("jsonschema")
    request = {
        "contract": CONTRACT,
        "run_id": "knowlab-run-0001",
        "owner_subject": "user-123",
        "manifest": manifest(),
        "cases": request_body()["cases"],
        "budget": {"limit": "20"},
        "timeout_seconds": 1800,
    }
    jsonschema.validate(request, _schema("test-run-request.schema.json"))
    answer = result("knowlab-run-0001", "failed").model_dump(mode="json")
    jsonschema.validate(answer, _schema("test-run-result.schema.json"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            answer | {"analysis": "不许带分析内容"},
            _schema("test-run-result.schema.json"),
        )
