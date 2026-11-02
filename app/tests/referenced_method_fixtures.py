"""Compiler-generated fixture for cross-application exact method references."""

from uuid import UUID

from app.domain.knowbots.compile import compile_manifest
from app.domain.knowbots.methods import BindMethod, FreezeMethod, bind, freeze
from app.domain.knowbots.workdraft import WorkdraftBody, cells
from tests.dynamic_method_fixtures import body


def build(*, output_file=None):
    raw = body()
    if output_file:
        raw["steps"][0]["advanced"] = {"output_file": output_file}
    definition = freeze(
        WorkdraftBody.model_validate(raw),
        FreezeMethod(
            request_id=UUID(int=1),
            revision=1,
            step_id="report",
            input_slots={"inspect": "records"},
        ),
    )
    ref = {
        "method_id": str(UUID(int=2)),
        "version": 2,
        "digest": definition.digest(),
        "inputs": {"records": "inspect"},
    }
    raw["method_refs"] = {"report": ref}
    raw["steps"][0] = bind(
        definition,
        WorkdraftBody.model_validate(raw),
        BindMethod(
            request_id=UUID(int=1),
            revision=1,
            alias="report",
            digest=definition.digest(),
            inputs={"records": "inspect"},
            required=True,
            max_calls=1,
        ),
    ).model_dump(mode="json")
    parsed = WorkdraftBody.model_validate(raw)
    sources = {
        path: {"kind": "user_text", "ref": f"record:{UUID(int=i + 1)}"}
        for i, path in enumerate(cells(parsed))
    }
    return compile_manifest(
        parsed,
        sources,
        pack_id="synthetic-references",
        version="1",
        runtime="knowbench",
        fallback_name="synthetic",
        method_sources={
            "report": {"reference": ref, "definition": definition.document()}
        },
    )
