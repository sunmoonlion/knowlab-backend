"""生成 `knowbot-test-run/v1` 的 JSON Schema。

契约在父仓 contracts/knowbot-test-run/v1/。

模型是唯一真源，测试核文件与模型逐字一致。改了模型就在后端目录下重新生成：
python -m app.domain.knowbots.schema
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.domain.knowbots.test_run import (
    AnswersV1,
    CaseOutputsV1,
    RunRequestV1,
    RunResultV1,
)

BASE = "https://contracts.sunmoonai.com/knowlab/knowbot-test-run/v1/"
DOCUMENTS = {
    "test-run-request.schema.json": (RunRequestV1, "knowbot-test-run v1 request"),
    "test-run-result.schema.json": (RunResultV1, "knowbot-test-run v1 result"),
    "test-run-answers.schema.json": (AnswersV1, "knowbot-test-run v1 answers"),
    "test-run-case-outputs.schema.json": (
        CaseOutputsV1,
        "knowbot-test-run v1 case outputs",
    ),
}


def document(name: str) -> dict[str, Any]:
    model, title = DOCUMENTS[name]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": BASE + name,
        **model.model_json_schema(),
        "title": title,
    }


def render(name: str) -> str:
    return json.dumps(document(name), ensure_ascii=False, indent=2) + "\n"


def manifest(texts: dict[str, str]) -> str:
    entries = {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in sorted(texts.items())
    }
    body = {"contract": "knowbot-test-run", "major_version": 1, "schemas": entries}
    return json.dumps(body, indent=2) + "\n"


def write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    texts = {name: render(name) for name in DOCUMENTS}
    for name, text in texts.items():
        (directory / name).write_text(text, encoding="utf-8")
    (directory / "contract-manifest.json").write_text(manifest(texts), encoding="utf-8")


if __name__ == "__main__":
    write(Path(__file__).resolve().parents[5] / "contracts/knowbot-test-run/v1")
