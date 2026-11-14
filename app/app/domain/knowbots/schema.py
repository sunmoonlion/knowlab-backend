"""生成 knowlab 当提供方的两份契约的 JSON Schema。

- `knowbot-test-run/v1`（运行方跑测试，SDD 0027 决定 12），在父仓
  contracts/knowbot-test-run/v1/；
- `knowbot-incubation/v1`（运行方跑孵化的一轮，专家孵化方案第八节），在父仓
  contracts/knowbot-incubation/v1/。

模型是唯一真源，测试核文件与模型逐字一致。改了模型就在后端目录下重新生成（两份一起）：
python -m app.domain.knowbots.schema
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.domain.knowbots.formal_method import FormalMethodDefinition
from app.domain.knowbots.incubation import IncubationRequestV1, IncubationResultV1
from app.domain.knowbots.test_run import (
    AnswersV1,
    CaseOutputsV1,
    RunRequestV1,
    RunResultV1,
)

TEST_RUN = "knowbot-test-run"
INCUBATION = "knowbot-incubation"
FORMAL_METHOD = "formal-method"
BASE = "https://contracts.sunmoonai.com/knowlab/knowbot-test-run/v1/"
DOCUMENTS: dict[str, tuple[type[BaseModel], str]] = {
    "test-run-request.schema.json": (RunRequestV1, "knowbot-test-run v1 request"),
    "test-run-result.schema.json": (RunResultV1, "knowbot-test-run v1 result"),
    "test-run-answers.schema.json": (AnswersV1, "knowbot-test-run v1 answers"),
    "test-run-case-outputs.schema.json": (
        CaseOutputsV1,
        "knowbot-test-run v1 case outputs",
    ),
}
INCUBATION_DOCUMENTS: dict[str, tuple[type[BaseModel], str]] = {
    "incubation-request.schema.json": (
        IncubationRequestV1,
        "knowbot-incubation v1 request",
    ),
    "incubation-result.schema.json": (
        IncubationResultV1,
        "knowbot-incubation v1 result",
    ),
}
# 契约名 → （$id 的前缀，几份 schema）
CONTRACTS: dict[str, tuple[str, dict[str, tuple[type[BaseModel], str]]]] = {
    FORMAL_METHOD: (
        "https://contracts.sunmoonai.com/knowlab/formal-method/v1/",
        {
            "definition.schema.json": (
                FormalMethodDefinition,
                "formal-method v1 definition",
            )
        },
    ),
    TEST_RUN: (BASE, DOCUMENTS),
    INCUBATION: (
        "https://contracts.sunmoonai.com/knowlab/knowbot-incubation/v1/",
        INCUBATION_DOCUMENTS,
    ),
}


def document(name: str, contract: str = TEST_RUN) -> dict[str, Any]:
    base, documents = CONTRACTS[contract]
    model, title = documents[name]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": base + name,
        **model.model_json_schema(),
        "title": title,
    }


def render(name: str, contract: str = TEST_RUN) -> str:
    return json.dumps(document(name, contract), ensure_ascii=False, indent=2) + "\n"


def manifest(texts: dict[str, str], contract: str = TEST_RUN) -> str:
    entries = {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in sorted(texts.items())
    }
    body = {"contract": contract, "major_version": 1, "schemas": entries}
    return json.dumps(body, indent=2) + "\n"


def write(directory: Path, contract: str = TEST_RUN) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    texts = {name: render(name, contract) for name in CONTRACTS[contract][1]}
    for name, text in texts.items():
        (directory / name).write_text(text, encoding="utf-8")
    (directory / "contract-manifest.json").write_text(
        manifest(texts, contract), encoding="utf-8"
    )


if __name__ == "__main__":
    contracts = Path(__file__).resolve().parents[5] / "contracts"
    for name in CONTRACTS:
        write(contracts / name / "v1", name)
