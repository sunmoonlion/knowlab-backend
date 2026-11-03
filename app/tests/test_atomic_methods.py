"""An immutable method is reusable under independent expert execution policies."""

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest

from app.domain.knowbots.atomic_methods import AtomicMethodDefinition, render
from app.domain.knowbots.compile import compile_manifest, decompile, pack_problems
from app.domain.knowbots.methods import (
    BindMethod,
    FreezeAtomicMethod,
    bind,
    freeze,
    resolve_execution,
)
from app.domain.knowbots.workdraft import WorkdraftBody
from tests.test_capability_methods import capability_body


def source_body():
    raw = capability_body()
    raw["steps"][0]["title"] = "条目标识覆盖核对"
    raw["steps"][0]["does"] = "逐项核对条目标识是否覆盖输入集合，缺项和重复项不得省略。"
    raw["steps"][0]["why"] = "逐条保留核对结果，避免漏项"
    raw["steps"][1]["outputs"].append({"name": "unrelated", "type": "text"})
    return WorkdraftBody.model_validate(raw)


def command():
    return FreezeAtomicMethod(
        definition_contract="knowlab/method-definition/v3",
        request_id=UUID(int=1),
        revision=1,
        step_id="report",
        input_slots={"inspect": "records", "review": "verification"},
        input_fields={"records": ("rows",), "verification": ("notes",)},
        input_descriptions={
            "records": "待汇总的核对条目",
            "verification": "可选复核结论",
        },
    )


def definition():
    value = freeze(source_body(), command())
    assert isinstance(value, AtomicMethodDefinition)
    return value


def binding(value, **overrides):
    return resolve_execution(
        BindMethod.model_validate(
            {
                "request_id": str(UUID(int=1)),
                "revision": 1,
                "alias": "report",
                "digest": value.digest(),
                "inputs": {"records": "inspect", "verification": "review"},
                "required": True,
                "max_calls": 2,
                "execution": {"on_fail": {"kind": "ask"}, "advanced": {"tools": []}},
                **overrides,
            }
        )
    )


def assembled():
    value = definition()
    cmd = binding(value)
    original = source_body()
    step = bind(value, original, cmd)
    doc = original.model_dump(mode="json")
    doc["steps"][0] = step.model_dump(mode="json")
    ref = {
        "method_id": str(UUID(int=2)),
        "version": 1,
        "digest": value.digest(),
        "inputs": cmd.inputs,
        "execution": cmd.execution.model_dump(mode="json"),
    }
    doc["method_refs"] = {"report": ref}
    sources = {"report": {"reference": ref, "definition": value.document()}}
    return doc, sources


def test_method_meaning_excludes_composition_and_execution():
    value = definition()
    assert "step" not in value.document()
    assert (
        not {"uses", "on_fail", "advanced", "selection", "output_file"}
        & value.document().keys()
    )
    assert value.inputs["records"].schema_ == {"rows": [{"id": "string"}]}
    assert not value.inputs["verification"].required
    before = value.document()
    first = bind(value, source_body(), binding(value))
    second = bind(
        value,
        source_body(),
        binding(
            value,
            required=False,
            max_calls=5,
            execution={
                "on_fail": {"kind": "back", "back_to": "inspect"},
                "advanced": {
                    "tools": [],
                    "reserve": "0.20",
                    "output_file": "summary.md",
                },
            },
        ),
    )
    assert first.does == second.does and first.checks == second.checks
    assert first.on_fail != second.on_fail and first.advanced != second.advanced
    assert value.document() == before


def test_method_can_be_authored_without_dynamic_expert_planning():
    from tests.dynamic_method_fixtures import body

    raw = body()
    raw.pop("planning")
    raw["steps"][0]["why"] = "逐条保留输入集合"
    for step in raw["steps"]:
        step.pop("selection", None)
    independent = freeze(
        WorkdraftBody.model_validate(raw),
        command().model_copy(
            update={
                "applies_when": "需要确认输入条目无遗漏时",
                "input_slots": {"inspect": "records"},
                "input_fields": {"records": ("rows",)},
                "input_descriptions": {"records": "待核对的条目"},
            }
        ),
    )
    assert isinstance(independent, AtomicMethodDefinition)
    assert independent.applies_when == "需要确认输入条目无遗漏时"


def test_compiler_and_import_roundtrip_keep_atomic_method_and_policy():
    doc, sources = assembled()
    manifest, errors = compile_manifest(
        WorkdraftBody.model_validate(doc),
        {},
        pack_id="atomic-example",
        version="1",
        fallback_name="example",
        runtime="knowbench",
        method_sources=sources,
    )
    assert not errors
    assert not pack_problems(manifest["pack"])
    restored, notes = decompile(manifest)
    assert not notes
    assert restored.method_refs["report"].execution.on_fail.kind == "ask"
    rebuilt, errors = compile_manifest(
        restored,
        {},
        pack_id="atomic-example",
        version="1",
        fallback_name="example",
        runtime="knowbench",
        method_sources=sources,
    )
    assert not errors and rebuilt == manifest


@pytest.mark.parametrize(
    "damage",
    ["schema", "digest", "missing_policy", "missing_field", "execution_in_method"],
)
def test_atomic_boundaries_fail_closed(damage):
    value = definition()
    cmd = binding(value).model_dump(mode="json")
    original = source_body().model_dump(mode="json")
    if damage == "schema":
        cmd["execution"]["advanced"]["output_schema"] = {"anything": "string"}
    elif damage == "digest":
        cmd["digest"] = "sha256:" + "0" * 64
    elif damage == "missing_policy":
        cmd.pop("execution")
    elif damage == "missing_field":
        original["steps"][1]["outputs"] = [{"name": "other", "type": "text"}]
    else:
        raw = deepcopy(value.document())
        raw["on_fail"] = {"kind": "ask"}
        with pytest.raises(ValueError):
            AtomicMethodDefinition.model_validate(raw)
        return
    with pytest.raises(ValueError):
        bind(
            value,
            WorkdraftBody.model_validate(original),
            BindMethod.model_validate(cmd),
        )


def test_provider_schema_and_canonical_reload():
    p = (
        Path(__file__).resolve().parents[3]
        / "contracts/author-method/v3/definition.schema.json"
    )
    assert p.read_text() == render()
    value = definition()
    # PostgreSQL jsonb changes object key order; no execution order is implied by it.
    reloaded = AtomicMethodDefinition.model_validate(
        json.loads(json.dumps(value.document(), sort_keys=True))
    )
    assert reloaded.digest() == value.digest()
    assert bind(reloaded, source_body(), binding(reloaded)) == bind(
        value, source_body(), binding(value)
    )


def test_runtime_consumes_compilation_and_rejects_changed_policy():
    doc, sources = assembled()
    manifest, errors = compile_manifest(
        WorkdraftBody.model_validate(doc),
        {},
        pack_id="atomic-example",
        version="1",
        runtime="knowbench",
        fallback_name="example",
        method_sources=sources,
    )
    assert not errors
    runtime = (
        Path(__file__).resolve().parents[4] / "knowbench-app/knowbench-backend/app"
    )
    probe = """
import json,sys
from copy import deepcopy
from app.domain.workbench.method_catalog import parse_catalog
pack=json.load(sys.stdin)
parse_catalog(pack)
for damage in ("reserve", "output", "failure", "source", "version"):
    bad=deepcopy(pack)
    unit=next(u for u in bad["method_units"] if u["contract"]["step_id"]=="report")
    if damage=="reserve": unit["contract"]["reserve"]="9"
    if damage=="output": unit["contract"]["output_schema"]={"invented":"string"}
    if damage=="failure": unit["contract"]["on_reject"]="rework"
    if damage=="source":
        bad["method_sources"]["report"]["definition"]["procedure"]="changed"
    if damage=="version": unit["contract"]["step_version"]="2"
    try: parse_catalog(bad)
    except ValueError: pass
    else: raise AssertionError(damage)
print("ok")
"""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    result = subprocess.run(
        [str(runtime / ".venv/bin/python"), "-c", probe],
        cwd=runtime,
        env=env,
        input=json.dumps(manifest["pack"]),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
