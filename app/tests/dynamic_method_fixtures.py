"""Synthetic non-financial author method; contains no user data or live calls."""

import uuid

from app.domain.knowbots.compile import compile_manifest
from app.domain.knowbots.workdraft import WorkdraftBody, cells


def body() -> dict:
    return {
        "name": "文档核对",
        "scope": {"does": "按作者标准核对给定文档"},
        "planning": {
            "method_text": "按问题选择核对方法。逐项核对后汇总，要求复核时再复核。",
            "final_units": ["report"],
            "max_steps": 8,
            "max_replans": 2,
            "reserve": "0.05",
            "input_max_chars": 8000,
        },
        # Catalog order is deliberately NOT execution order.
        "steps": [
            {
                "id": "report",
                "title": "汇总",
                "does": "列出每一条的核对结果。",
                "uses": ["inspect"],
                "selection": {
                    "applies_when": "交回最终结果时",
                    "required": True,
                    "max_calls": 1,
                },
                "outputs": [{"name": "rows", "type": "list"}],
                "checks": [
                    {
                        "template": "covers",
                        "params": {
                            "path": "rows",
                            "field": "id",
                            "source_step": "inspect",
                            "source_path": "rows",
                            "source_field": "id",
                        },
                    }
                ],
                "advanced": {"tools": []},
                "on_fail": {"kind": "back", "back_to": "inspect"},
            },
            {
                "id": "inspect",
                "title": "核对",
                "does": "只依据给定文档核对，不补充未提供的文字。",
                "selection": {
                    "applies_when": "需要核对文档时",
                    "required": True,
                    "max_calls": 3,
                },
                "outputs": [{"name": "rows", "type": "list"}],
                "checks": [{"template": "list_min", "params": {"path": "rows"}}],
                "advanced": {"tools": ["read_input"]},
            },
            {
                "id": "review",
                "title": "复核",
                "does": "再按同一原文逐项复核。",
                "uses": ["inspect"],
                "selection": {
                    "applies_when": "用户要求复核时",
                    "required": False,
                    "max_calls": 2,
                },
                "outputs": [{"name": "notes", "type": "text"}],
                "advanced": {"tools": ["read_input"]},
            },
        ],
    }


def build(doc: dict | None = None) -> tuple[dict, list]:
    parsed = WorkdraftBody.model_validate(doc if doc is not None else body())
    sources = {
        path: {"kind": "user_text", "ref": f"record:{uuid.UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed))
    }
    return compile_manifest(
        parsed,
        sources,
        pack_id="synthetic-methods",
        version="1",
        runtime="knowbench",
        fallback_name="synthetic",
    )
