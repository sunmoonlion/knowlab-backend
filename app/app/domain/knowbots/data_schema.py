"""生成 `knowbot-data/v1` 的 JSON Schema（SDD 0027 §3.5）。

契约在父仓 contracts/knowbot-data/v1/。模型是唯一真源，测试核文件与模型逐字一致。
改了模型就在后端目录下重新生成：python -m app.domain.knowbots.data_schema
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.domain.knowbots.data_read import (
    ListRequestV1,
    ListResultV1,
    ReadRequestV1,
    ReadResultV1,
)

BASE = "https://contracts.sunmoonai.com/knowlab/knowbot-data/v1/"
DOCUMENTS = {
    "data-list-request.schema.json": (ListRequestV1, "knowbot-data v1 list request"),
    "data-list-result.schema.json": (ListResultV1, "knowbot-data v1 list result"),
    "data-read-request.schema.json": (ReadRequestV1, "knowbot-data v1 read request"),
    "data-read-result.schema.json": (ReadResultV1, "knowbot-data v1 read result"),
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
    body = {"contract": "knowbot-data", "major_version": 1, "schemas": entries}
    return json.dumps(body, indent=2) + "\n"


def write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    texts = {name: render(name) for name in DOCUMENTS}
    for name, text in texts.items():
        (directory / name).write_text(text, encoding="utf-8")
    (directory / "contract-manifest.json").write_text(manifest(texts), encoding="utf-8")


if __name__ == "__main__":
    write(Path(__file__).resolve().parents[5] / "contracts/knowbot-data/v1")
