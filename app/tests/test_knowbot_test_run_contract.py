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


def test_step_results_may_say_why_a_check_failed() -> None:
    """v1 冻结后加的可选字段 details（审读第 3 条）：带了收、不带也收、超长拒。"""
    from pydantic import ValidationError

    from app.domain.knowbots.test_run import StepResult

    base = {"step_id": "answer", "accepted": False, "failed_checks": ["取数"]}
    assert StepResult.model_validate(base).details == ()
    said = StepResult.model_validate(base | {"details": ["取数: says 150, data 100"]})
    assert said.details == ("取数: says 150, data 100",)
    with pytest.raises(ValidationError):
        StepResult.model_validate(base | {"details": ["长" * 201]})


def test_a_running_result_may_say_how_far_it_got() -> None:
    """v1 冻结后加的可选字段 progress（待办账 135）：带了收、不带也收，只收计数和钱。"""
    from pydantic import ValidationError

    from app.domain.knowbots.test_run import RunResultV1

    base = {
        "contract": "knowbot-test-run/v1",
        "run_id": "knowlab-run-0001",
        "status": "running",
    }
    assert RunResultV1.model_validate(base).progress is None
    progress = {
        "case_id": "c1",
        "cases_done": 0,
        "cases_total": 3,
        "step": 3,
        "steps_total": 8,
        "step_title": "勾稽",
        "attempt": 1,
        "spent": "12.403100",
    }
    got = RunResultV1.model_validate(base | {"progress": progress})
    assert got.progress is not None and got.progress.step == 3
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(
        got.model_dump(mode="json"), _schema("test-run-result.schema.json")
    )
    with pytest.raises(ValidationError):
        RunResultV1.model_validate(
            base | {"progress": progress | {"answer": "不许带内容"}}
        )
    queued = progress | {"queued_behind": "03e447a0-0000-4000-8000-000000000000"}
    got = RunResultV1.model_validate(base | {"progress": queued})
    jsonschema.validate(
        got.model_dump(mode="json"), _schema("test-run-result.schema.json")
    )
    with pytest.raises(ValidationError):  # 只许一个委托编号，不许带别的话
        RunResultV1.model_validate(
            base | {"progress": progress | {"queued_behind": "排在张三的委托后面"}}
        )


def test_an_accepted_result_may_say_what_it_waits_for() -> None:
    """v1 冻结后加的可选字段 blocked（账 141）：已受理还没开跑时，写明在等
    测试电脑上线，还是等干净的目录；别的状态不许带。"""
    from pydantic import ValidationError

    from app.domain.knowbots.test_run import RunResultV1

    base = {
        "contract": "knowbot-test-run/v1",
        "run_id": "knowlab-run-0001",
        "status": "accepted",
    }
    assert RunResultV1.model_validate(base).blocked is None
    jsonschema = pytest.importorskip("jsonschema")
    for reason in ("computer_offline", "folder_not_ready"):
        got = RunResultV1.model_validate(base | {"blocked": reason})
        assert got.blocked == reason
        jsonschema.validate(
            got.model_dump(mode="json"), _schema("test-run-result.schema.json")
        )
    with pytest.raises(ValidationError):
        RunResultV1.model_validate(base | {"blocked": "sleeping"})
    with pytest.raises(ValidationError):
        RunResultV1.model_validate(
            base | {"status": "running", "blocked": "computer_offline"}
        )
