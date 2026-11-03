"""Exact method references and their derived expert projections.

Definitions are immutable evidence, not another editable master. Only the
knowlab compiler translates author cells into executable contracts. Consumers
pin that complete compilation as part of the expert manifest digest.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from app.domain.knowbots.methods import AnyMethodDefinition, BindMethod, bind
from app.domain.knowbots.workdraft import (
    MethodReference,
    StepCell,
    Strict,
    WorkdraftBody,
)


class MethodSource(Strict):
    reference: MethodReference
    definition: AnyMethodDefinition

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


def projection(body: WorkdraftBody, alias: str, source: MethodSource) -> StepCell:
    step = next(s for s in body.steps if s.id == alias)
    if step.selection is None:
        raise ValueError("referenced method needs expert selection bounds")
    return bind(
        source.definition,
        body,
        BindMethod(
            request_id=UUID(int=0),
            revision=1,
            alias=alias,
            digest=source.reference.digest,
            inputs=source.reference.inputs,
            required=step.selection.required,
            max_calls=step.selection.max_calls,
            execution=source.reference.execution,
        ),
    )


def validate_sources(body: WorkdraftBody, sources: dict[str, Any]) -> None:
    refs = body.method_refs or {}
    if set(refs) != set(sources):
        raise ValueError("every reference needs its exact method snapshot")
    steps = {s.id: s for s in body.steps}
    for alias, raw in sources.items():
        source = MethodSource.model_validate(raw)
        if source.document() != raw:
            raise ValueError(
                "method snapshots must retain their exact canonical content"
            )
        if source.reference != refs[alias]:
            raise ValueError("method snapshot differs from the pinned reference")
        if projection(body, alias, source) != steps[alias]:
            raise ValueError("referenced method projection has been edited")


def restore(manifest: dict[str, Any]) -> WorkdraftBody:
    """Import without converting references into independently editable copies.

    Recompile against embedded exact snapshots and reject any lossy conversion.
    The application then resolves those references against the author's registry.
    """
    from app.domain.knowbots.compile import PLATFORM_TAIL, _compile_manifest, decompile

    pack = manifest["pack"]
    if pack.get("method_compiler") != "knowlab/workdraft-compiler/v1":
        raise ValueError("unsupported method compiler")
    raw_sources = pack.get("method_sources")
    if not isinstance(raw_sources, dict) or not 1 <= len(raw_sources) <= 30:
        raise ValueError("referenced catalog requires method_sources")
    sources = {
        alias: MethodSource.model_validate(raw) for alias, raw in raw_sources.items()
    }
    plain = deepcopy(manifest)
    plain["pack"].pop("method_sources")
    plain["pack"].pop("method_compiler")
    plain["pack"]["runtime_contract"] = "knowbench/dynamic-methods/v1"
    for unit in plain["pack"]["method_units"]:
        if unit["contract"]["step_id"] in sources:
            unit["contract"].pop("step_version", None)
    body, _ = decompile(plain)
    # Replace decompiled engineering defaults with the exact author definition.
    projected = {
        alias: projection(body, alias, source) for alias, source in sources.items()
    }
    body = WorkdraftBody.model_validate(
        {
            **body.model_dump(mode="json"),
            "steps": [
                projected.get(s.id, s).model_dump(mode="json") for s in body.steps
            ],
            "method_refs": {
                alias: s.reference.model_dump(mode="json")
                for alias, s in sources.items()
            },
        }
    )
    validate_sources(body, raw_sources)
    # Replay immutable v1 evidence byte-for-byte, not today's authoring policy.
    # Saving/binding/testing/publishing the imported draft still calls the public
    # compiler and must satisfy its current authoring checks.
    rebuilt, problems = _compile_manifest(
        body,
        {},
        pack_id=pack["pack_id"],
        version=pack["version"],
        runtime=manifest.get("runtime", "knowbench"),
        fallback_name=pack["title"],
        method_sources=raw_sources,
        # Old frozen file-producing definitions contained the read-only tail.
        # Replay that exact historical template for digest verification only;
        # new author compilation always emits the file-producing template.
        legacy_file_tail=any(
            unit["contract"].get("output_file")
            and unit["contract"]["method_text"].endswith(PLATFORM_TAIL)
            for unit in pack["method_units"]
        ),
    )
    if problems or rebuilt["pack"]["method_units"] != pack["method_units"]:
        raise ValueError("compiled methods differ from their pinned definitions")
    return body
