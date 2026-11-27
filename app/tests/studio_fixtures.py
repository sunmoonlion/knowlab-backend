"""Create a studio draft from the exact formal definition used by a test."""

from uuid import UUID

from app.application.services.method_drafts import (
    HandoffSeedIn,
    MethodDraftService,
    PatchMethodDraftIn,
)
from app.domain.knowbots.formal_method import ExpectedDeliverableSpec, MethodDraftV2
from app.domain.knowbots.formal_method_admission import FormalMethodDraft


def studio_contract(formal):
    definition = formal.definition
    declaration = definition.capability_declaration
    outputs = definition.interfaces()["outputs"]
    expected = []
    for rule in definition.how_to_validate:
        slot = outputs[rule.outputs[0]]["slot"]
        condition = declaration.conditions.get(rule.promise_id)
        expected.append(
            ExpectedDeliverableSpec(
                promise_id=rule.promise_id,
                summary=declaration.promises[rule.promise_id],
                expected_domain_id=slot["meaning"]["domain_id"],
                expected_schema=slot["schema"],
                delivery_mode="conditional" if condition else "always",
                condition_description=condition.when if condition else None,
                condition_input_hint=condition.input_slots[0]
                if condition and condition.input_slots
                else None,
            )
        )
    draft = MethodDraftV2(
        method_kind="tool" if definition.procedure.kind == "direct" else "expert",
        title=declaration.title,
        purpose=declaration.purpose,
        applies_when=declaration.applies_when,
        excludes=declaration.excludes,
        expected_deliverables=expected,
        access_ceiling=definition.access_requirements,
        procedure=definition.procedure,
        validation_rules=definition.how_to_validate,
        approval_rules=definition.what_requires_approval,
    )
    assert draft.to_definition().digest() == definition.digest()
    return draft


async def save_studio_method(repo, owner, request_id, method_id, revision, document):
    formal = FormalMethodDraft.model_validate(document)
    draft = studio_contract(formal)
    declaration = formal.definition.capability_declaration
    service = MethodDraftService(repo)
    if revision:
        return await service.patch(
            owner,
            str(method_id),
            PatchMethodDraftIn(
                revision=revision,
                draft_contract=draft,
                source=formal.source,
                allowed_callers=formal.allowed_callers,
            ),
        )
    return await service.create_from_handoff(
        owner,
        HandoffSeedIn(
            title=declaration.title,
            goal=declaration.purpose,
            draft_contract=draft,
        ),
        method_id=method_id,
        request_id=UUID(str(request_id)),
        source=formal.source,
        allowed_callers=formal.allowed_callers,
    )
