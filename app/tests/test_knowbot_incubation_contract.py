"""孵化契约 knowbot-incubation/v1（专家孵化方案第八节）。

父仓的 schema 就是从模型生成的那一份；请求与交回物的规矩（判断只出缺口、不碰
工程层、出处按种类引）在模型里守住。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.knowbots.incubation import (
    CONTRACT,
    IncubationRequestV1,
    IncubationResultV1,
)
from app.domain.knowbots.schema import INCUBATION, INCUBATION_DOCUMENTS, render

CONTRACT_DIR = Path(__file__).resolve().parents[3] / "contracts/knowbot-incubation/v1"
REGENERATE = "regenerate: python -m app.domain.knowbots.schema"
ENV = "7a1f0c2e-5b6d-4e8f-9a0b-1c2d3e4f5a6b"


def _require_contract() -> None:
    if not CONTRACT_DIR.exists():  # 只拿到后端子仓时（例如镜像构建上下文）没有父仓
        pytest.skip("parent contracts/ is not checked out")


def _schema(name: str) -> dict:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


def request(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract": CONTRACT,
        "incubation_id": "knowlab-inc-0001",
        "owner_subject": "user-123",
        "action": "revise",
        "instruction": "第 2 步拆成两步",
        "step_id": "trend",
        "seed": {"kind": "question", "text": "这家公司未来三年的收入会怎样？"},
        "workdraft": {
            "body": {
                "name": "财务分析与预测",
                "steps": [
                    {"id": "scan", "title": "数据摸底", "does": "看清有哪些表"},
                    {
                        "id": "trend",
                        "title": "看趋势",
                        "rules": [{"id": "r1", "text": "收入按合并口径"}],
                    },
                ],
            },
            "sources": {
                "name": {"kind": "user_text", "ref": "record:x"},
                "steps/trend/rules/r1": {"kind": "user_confirmed", "ref": "question:q"},
            },
        },
        "answers": [
            {
                "id": "q-1",
                "question": "收入用合并还是母公司口径？",
                "proposal": "合并",
                "answer": "按提议",
            }
        ],
        "data_formats": [
            {
                "name": "利润表",
                "tables": [
                    {
                        "name": "利润表",
                        "columns": ["2023", "2024"],
                        "rows": ["营业收入"],
                    }
                ],
            }
        ],
        "budget": {"limit": "20"},
        "timeout_seconds": 1800,
        "runner": {"kind": "user", "environment_id": ENV, "workspace_root": "/w"},
    }
    return body | overrides


def handback() -> dict[str, Any]:
    return {
        "sources": [
            {"path": "steps/scan/title", "kind": "idea", "quote": "收入会怎样"},
            {"path": "steps/trend/rules/r2", "kind": "answer", "ref": "q-1"},
            {"path": "steps/split/title", "kind": "proposal"},
        ],
        "gaps": [
            {
                "id": "g1",
                "question": "预测用什么方法？",
                "proposal": "按近三年复合增速外推",
                "options": ["复合增速外推", "分业务预测"],
            }
        ],
        "suggestions": [
            {
                "id": "s1",
                "path": "steps/split",
                "op": "add",
                "value": {"title": "拆分业务", "does": "按业务拆开看收入"},
                "reason": "作者要把第 2 步拆成两步",
            },
            {
                "id": "s2",
                "path": "steps/split",
                "op": "move",
                "value": 2,
                "reason": "放在后面",
            },
            {"id": "s3", "path": "steps/scan/why", "op": "remove", "reason": "重复"},
        ],
        "self_check": [{"suggestion_id": "s9", "turned_into_gap": "g1"}],
    }


def result(status: str = "succeeded", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract": CONTRACT,
        "incubation_id": "knowlab-inc-0001",
        "status": status,
        "cost": {"spent": "0.120000"},
        "task_id": "03e447a0-0000-4000-8000-000000000000",
        "started_at": "2026-10-28T00:00:00+00:00",
        "finished_at": None,
    }
    if status == "succeeded":
        body["handback"] = handback()
    return body | extra


@pytest.mark.parametrize("name", sorted(INCUBATION_DOCUMENTS))
def test_schema_files_are_generated_from_the_models(name: str) -> None:
    _require_contract()
    assert (CONTRACT_DIR / name).read_text(encoding="utf-8") == render(
        name, INCUBATION
    ), REGENERATE


def test_contract_manifest_digests_match_the_schemas() -> None:
    _require_contract()
    release = json.loads((CONTRACT_DIR / "contract-manifest.json").read_text())
    assert release["contract"] == "knowbot-incubation"
    assert release["major_version"] == 1
    assert set(release["schemas"]) == set(INCUBATION_DOCUMENTS)
    for name, digest in release["schemas"].items():
        assert digest == hashlib.sha256((CONTRACT_DIR / name).read_bytes()).hexdigest()


def test_examples_satisfy_the_published_schemas() -> None:
    _require_contract()
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(request(), _schema("incubation-request.schema.json"))
    draft = {
        k: v
        for k, v in request(action="draft").items()
        if k not in ("instruction", "step_id")
    }
    jsonschema.validate(draft, _schema("incubation-request.schema.json"))
    result_schema = _schema("incubation-result.schema.json")
    for body in (
        result(),
        result("running"),
        result("waiting", waiting_for="computer_offline"),
        result("over_budget", error="the budget ran out"),
    ):
        jsonschema.validate(
            IncubationResultV1.model_validate(body).model_dump(mode="json"),
            result_schema,
        )
    with pytest.raises(jsonschema.ValidationError):  # 建议不许碰「高级」
        bad = result()
        bad["handback"]["suggestions"][0]["path"] = "steps/scan/advanced"
        jsonschema.validate(bad, result_schema)
    with pytest.raises(jsonschema.ValidationError):  # 交过去的只有列死的字段
        jsonschema.validate(
            request() | {"data": [{"营业收入": 100}]},
            _schema("incubation-request.schema.json"),
        )


def test_the_request_carries_only_formats_and_the_authors_own_material() -> None:
    got = IncubationRequestV1.model_validate(request())
    assert got.workdraft.body.steps[1].rules[0].text == "收入按合并口径"
    assert got.data_formats[0].tables[0].rows == ("营业收入",)
    for wrong in (
        request(instruction=None),  # 按指令改要有作者的那句话
        request(action="draft"),  # 起草不带指令
        request(step_id="nope"),  # 指定的那一步要在工作稿里
        request(seed={"kind": "excel", "text": "x"}),
        request(
            data_formats=[{"name": "利润表", "tables": [{"rows": [100]}]}]
        ),  # 只有格式，没有数
        request(budget={"limit": "1.234"}),
        request(budget={"limit": "0"}),  # 零不是「不设上限」
        request(runner={"kind": "platform"}),
        request(answers=[request()["answers"][0]] * 2),
    ):
        with pytest.raises(ValidationError):
            IncubationRequestV1.model_validate(wrong)


REPORT = {
    "number": 3,
    "draft_version": "2.1",
    "status": "failed",
    "cases": [
        {
            "case_id": "c1",
            "question": "分析 2025 年报",
            "status": "failed",
            "steps": [
                {
                    "step_id": "scan",
                    "accepted": False,
                    "failed_checks": ["sourced"],
                    "details": ["毛利率对不上原表"],
                }
            ],
            "expectations": [
                {
                    "artifact": "summary",
                    "path": "gross_margin",
                    "kind": "approx",
                    "passed": False,
                }
            ],
        }
    ],
}


def test_formats_carry_the_description_and_readings_and_revise_a_report() -> None:
    """数据的格式加了描述与读法；「让 AI 改…」可以带最近一次测试的报告（第 8.4 节）。"""
    fmt = {
        "name": "2024 年报.pdf",
        "media_type": "application/pdf",
        "description": {"subject": "600519.SH", "doc_type": "annual_report"},
        "readings": ["text", "pages"],
    }
    got = IncubationRequestV1.model_validate(
        request(data_formats=[fmt], test_report=REPORT)
    )
    assert got.data_formats[0].readings == ("text", "pages")
    assert got.test_report is not None and got.test_report.cases[0].steps[0].details
    expectation = REPORT["cases"][0]["expectations"][0]
    for wrong in (
        request(action="draft", instruction=None, step_id=None, test_report=REPORT),
        request(data_formats=[fmt | {"readings": ["正文"]}]),
        request(data_formats=[fmt | {"description": {"content": "营业收入 100"}}]),
        request(
            test_report=REPORT
            | {
                "cases": [
                    REPORT["cases"][0]
                    | {"expectations": [expectation | {"value": 0.52}]}
                ]
            }
        ),  # 期望值不给
        request(test_report=REPORT | {"status": "running"}),
    ):
        with pytest.raises(ValidationError):
            IncubationRequestV1.model_validate(wrong)
    _require_contract()
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(
        got.model_dump(mode="json"), _schema("incubation-request.schema.json")
    )


def test_a_round_hands_back_only_on_success_and_says_what_it_waits_for() -> None:
    assert IncubationResultV1.model_validate(result()).final
    assert not IncubationResultV1.model_validate(result("queued")).final
    for wrong in (
        result("failed") | {"handback": handback()},
        {k: v for k, v in result().items() if k != "handback"},
        result("waiting"),
        result("running", waiting_for="computer_offline"),
        result("timeout"),
    ):
        with pytest.raises(ValidationError):
            IncubationResultV1.model_validate(wrong)


def _with(**change: Any) -> dict[str, Any]:
    body = result()
    for key, value in change.items():
        body["handback"][key] = value
    return body


def test_suggestions_stay_on_the_method_layer_and_judgments_are_gaps() -> None:
    """判断只出缺口（第四节）：建议只碰方法层的格子，不写 method_text、不碰「高级」
    与挑的数据；出处要么逐字引创意、要么指答过的缺口、要么照实标提议；自查改成的
    缺口要在缺口表里。"""
    suggestion = handback()["suggestions"][0]
    for wrong in (
        _with(suggestions=[suggestion | {"path": "steps/scan/advanced"}]),
        _with(suggestions=[suggestion | {"path": "steps/scan/method_text"}]),
        _with(suggestions=[suggestion | {"path": "data_picks"}]),
        _with(suggestions=[suggestion | {"path": "steps"}]),
        _with(
            suggestions=[
                suggestion | {"value": {"title": "x", "advanced": {"tools": []}}}
            ]
        ),
        _with(suggestions=[suggestion | {"value": {"method_text": "Do x"}}]),
        _with(suggestions=[suggestion | {"op": "remove"}]),  # 删不带 value
        _with(suggestions=[suggestion | {"op": "move", "value": "first"}]),
        _with(suggestions=[suggestion | {"value": None}]),
        _with(suggestions=[suggestion | {"reason": ""}]),  # 每条带理由
        _with(suggestions=[suggestion, suggestion]),
        _with(sources=[{"path": "name", "kind": "idea"}]),  # 引创意要有原话
        _with(sources=[{"path": "name", "kind": "answer"}]),  # 答过的缺口要指明哪一个
        _with(sources=[{"path": "name", "kind": "proposal", "quote": "x"}]),
        _with(sources=[{"path": "name", "kind": "guess"}]),
        _with(gaps=[handback()["gaps"][0] | {"proposal": ""}]),  # 缺口附提议
        _with(self_check=[{"suggestion_id": "s9", "turned_into_gap": "g9"}]),
        _with(self_check=[{"suggestion_id": "s1", "turned_into_gap": "g1"}]),
    ):
        with pytest.raises(ValidationError):
            IncubationResultV1.model_validate(wrong)
