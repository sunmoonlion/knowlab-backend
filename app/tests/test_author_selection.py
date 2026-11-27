"""Regression cases for editable business proposals against pinned tools."""

from copy import deepcopy
from uuid import uuid4

import pytest

from app.domain.knowbots.formal_method_compiler import compile_method
from app.domain.knowbots.selection import Selection, expand, required_features
from app.domain.knowbots.workdraft import MethodTemplate
from tests.test_formal_method import frozen, resolved, slot, tool
from tests.test_formal_method_plan import comparison_graph

FLAGS = dict.fromkeys(
    ("sequence", "join", "independent_review", "object_batch", "conditional"), True
)
TEMPLATE = MethodTemplate(
    template="checked_delivery/v1",
    goal="复核",
    deliverable="复核结果",
    check="逐项一致",
    evidence="原表",
)


def build(steps, methods, patterns, *, batch=None, cases=None, flags=FLAGS):
    proposal = Selection(
        status="ready",
        reason="按作者的要求分工",
        routing_required="P5" in patterns,
        steps=steps,
        patterns=patterns,
        object_batch=batch,
    )
    draft, roles = expand(
        proposal,
        methods,
        TEMPLATE,
        flags,
        required_cases=cases or {},
        title="作者的专家",
    )
    TEMPLATE.check_definition(draft)
    assert draft.definition.capability_declaration.title == "作者的专家"
    compile_method(uuid4(), draft.definition, resolved(*methods.values()))
    return draft, roles


def step(name, method, inputs, **extra):
    return dict(id=name, method=method, task="核对 " + name, inputs=inputs, **extra)


def output(name):
    return dict(source="step", step=name, output="result")


def test_new_schema_and_legacy_handbacks_use_the_same_step_contract():
    assert "patterns" not in Selection.model_json_schema()["properties"]
    fields = dict(
        status="ready",
        reason="直接采用",
        routing_required=False,
        steps=[step("direct", "m1", {})],
    )
    fresh = Selection.model_validate(fields).model_dump(mode="json")
    assert "patterns" not in fresh
    legacy = Selection.model_validate({**fresh, "patterns": []})
    assert legacy.model_dump(mode="json") == {**fresh, "patterns": []}


def test_common_prefix_retains_mandatory_dispatch_and_requires_review_of_each_label():
    original = tool()
    original["procedure"]["inputs"]["kind"] = {**slot(), "schema": {"type": "string"}}
    methods = {"first": frozen(), "a": frozen(original), "b": frozen(original)}
    steps = [
        step("prepare", "first", {"value": {"source": "caller", "name": "rows"}}),
        step(
            "dispatch",
            "a",
            {"value": output("prepare"), "kind": {"source": "caller", "name": "label"}},
            route_input="kind",
            cases={"A": "a", "B": "b"},
        ),
    ]
    draft, _ = build(steps, methods, ["P1", "P5"], cases={"dispatch": {"A", "B"}})
    assert (
        draft.definition.procedure.steps[1].candidate_choice.match("B")
        == methods["b"].reference
    )
    with pytest.raises(ValueError, match="selection_route"):
        build(steps, methods, ["P1", "P5"], cases={"dispatch": {"A"}})
    del steps[1]["route_input"], steps[1]["cases"]
    with pytest.raises(ValueError, match="selection_route"):
        build(steps, methods, ["P1"], cases={"dispatch": {"A", "B"}})


def test_independent_review_includes_its_own_join():
    src = tool()
    src["procedure"]["implementation"].update(
        kind="registered_instruction", source=str(uuid4()), entry="instruction"
    )
    src["procedure"]["max_model_turns"] = 1
    join = deepcopy(src)
    join["procedure"]["inputs"] = {"left": slot(), "right": slot()}
    methods = {"one": frozen(src), "two": frozen(src), "compare": frozen(join)}
    steps = [
        step("left", "one", {"value": {"source": "caller", "name": "data"}}),
        step("right", "two", {"value": {"source": "caller", "name": "data"}}),
        step("review", "compare", {"left": output("left"), "right": output("right")}),
    ]
    assert build(steps, methods, ["P2"])[1] is None
    steps[-1]["reviews"] = ["left", "right"]
    _, roles = build(steps, methods, ["P3"])
    assert set(roles["review"]["handoff_from"]) == {"left", "right"}
    assert build(steps, methods, ["P2", "P3"])[1] == roles
    assert required_features(
        Selection.model_validate(
            dict(
                status="ready",
                reason="复核",
                patterns=["P2", "P3"],
                steps=steps,
                routing_required=False,
            )
        )
    ) == {"independent_review", "join"}
    with pytest.raises(ValueError, match="selection_capability"):
        build(steps, methods, [], flags={**FLAGS, "join": False})
    short = [
        steps[0],
        step("review", "two", {"value": output("left")}, reviews=["left"]),
    ]
    assert build(short, methods, ["P3"])[1]["review"]["handoff_from"] == ["left"]
    with pytest.raises(ValueError, match="selection_capability"):
        build(steps, methods, ["P3"], flags={**FLAGS, "independent_review": False})


def test_object_rows_are_bound_to_the_actual_worker_result():
    graph = comparison_graph()["procedure"]
    steps = []
    methods = {}
    for actual in graph["steps"]:
        child = tool()
        child["procedure"]["inputs"] = {
            k: i["slot"] for k, i in actual["inputs"].items()
        }
        methods[actual["id"]] = frozen(child)
        steps.append(
            step(
                actual["id"],
                actual["id"],
                {k: i["binding"] for k, i in actual["inputs"].items()},
            )
        )
    draft, _ = build(steps, methods, ["P4"], batch=graph["object_batch"])
    assert draft.definition.procedure.object_batch.max_objects == 3
    with pytest.raises(ValueError):
        build(steps, {}, ["P4"], batch=graph["object_batch"])


@pytest.mark.parametrize("status", ["clarify", "gap"])
def test_unresolved_status_cannot_be_confirmed(status):
    proposal = Selection.model_validate(
        dict(
            status=status,
            reason="先确认阈值",
            steps=[],
            patterns=[],
            routing_required=True,
        )
    )
    with pytest.raises(ValueError, match="selection_unresolved"):
        expand(proposal, {}, TEMPLATE, FLAGS, required_cases={})


@pytest.mark.asyncio
@pytest.mark.parametrize("independent", [False, True])
async def test_frozen_directory_round_rechecks_pins_and_records_the_adopted_graph(
    monkeypatch,
    independent,
):
    from unittest.mock import AsyncMock

    from app.application.errors.exceptions import NotFoundError
    from app.application.services.incubation_selection import SelectionService
    from app.application.services.knowbot_incubation import KnowbotIncubationService
    from app.application.services.knowbot_service import KnowbotCreationService
    from app.domain.knowbots.drafts import KnowbotRequestIn
    from app.domain.knowbots.formal_method_admission import FreezeFormalMethod
    from app.domain.knowbots.incubation import IncubationResultV1
    from tests.knowbot_fakes import OWNER, FakeIncubator, own_computer, request_body
    from tests.test_formal_method_execution import complete_trial, prepared

    repo, runtime, methods, mid, command = await prepared()
    await methods.execute(OWNER, str(mid), command)
    complete_trial(runtime, command, business=True)
    version = await methods.freeze(
        OWNER,
        str(mid),
        1,
        FreezeFormalMethod(
            request_id=command.request_id,
            revision=command.revision,
            run_id=command.run_id,
            visibility="private",
        ),
    )
    monkeypatch.setattr(
        runtime,
        "incubation_tool_versions",
        AsyncMock(return_value={"items": [], "next": None}),
        raising=False,
    )
    monkeypatch.setattr(
        runtime,
        "formal_method_sources",
        AsyncMock(return_value={"items": []}),
        raising=False,
    )
    monkeypatch.setattr(
        runtime,
        "planning_capabilities",
        AsyncMock(return_value={"patterns": FLAGS}),
        raising=False,
    )
    own_computer(repo)
    request = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    rid = str(request.id)
    runner = FakeIncubator()
    rounds = KnowbotIncubationService(repo, runner, runtime)
    from app.application.services.method_drafts import HandoffSeedIn, MethodDraftService
    from app.domain.knowbots.formal_method import MethodDraftV2

    studio = {"title": "核验", "purpose": "核验", "method_kind": "expert"}
    if not independent:
        studio.update(
            applies_when=["输入原表"],
            excludes=["预测"],
            expected_deliverables=[
                dict(
                    promise_id="reconciled",
                    summary="按原表核验",
                    expected_domain_id="core.number.value",
                    expected_schema={"type": "number"},
                )
            ],
            validation_rules=[
                dict(
                    promise_id="reconciled",
                    outputs=["result"],
                    check="复算",
                    evidence="原始值",
                    on_failure="fail",
                )
            ],
        )
    mid = uuid4()
    await MethodDraftService(repo).create_from_handoff(
        OWNER,
        HandoffSeedIn(
            title="核验",
            goal="核验",
            draft_contract=MethodDraftV2.model_validate(studio),
        ),
        method_id=mid,
        request_id=request.id,
    )
    record = await rounds.start(OWNER, rid, action="draft", selection_revision=1)
    pin = record.seed["selection_context"]["methods"]["m1"]
    assert pin["reference"]["digest"] == version.digest
    assert pin["inputs"]["value"]["schema"] == {"type": "number"}
    assert "definition" not in pin
    # The published pin can still be chosen from a subsequent directory page.
    from app.domain.knowbots.formal_method import MethodRef

    picked = MethodRef.model_validate(pin["reference"])
    lookup = runtime.method_skill_center_incubation_methods

    async def later_page(*args, **kwargs):
        if not kwargs.get("references"):
            return []
        return await lookup(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "method_skill_center_incubation_methods", later_page)
        catalog = await SelectionService(repo, runtime).catalog(
            OWNER, rid, preferred=picked
        )
        assert catalog["methods"]["m1"]["reference"] == pin["reference"]
        wrong_digest = picked.model_copy(update={"digest": "sha256:" + "0" * 64})
        denied = await SelectionService(repo, runtime).catalog(
            OWNER, rid, preferred=wrong_digest
        )
        assert not denied["methods"]

    steps = [
        step("read", "m1", {"value": dict(source="caller", name="input")}),
        step("check", "m1", {"value": output("read")}),
    ]
    if independent:
        steps[0]["deliver_as"] = {"result": "evidence"}
        steps[1]["deliver_as"] = {"result": "difference"}
        steps[1]["inputs"] = {"value": dict(source="caller", name="closing")}
    choice = dict(
        status="ready",
        steps=steps,
        patterns=[] if independent else ["P1"],
        routing_required=False,
        reason="按两个来源分别核对" if independent else "前一步的结果交给后一步",
    )
    runner.results[record.run_id] = IncubationResultV1.model_validate(
        dict(
            contract="knowbot-incubation/v1",
            incubation_id=record.run_id,
            status="succeeded",
            handback={"selection": choice},
        )
    )
    record = await rounds.refresh(OWNER, rid, str(record.id))

    async def adopt():
        return await SelectionService(repo, runtime).materialize(
            OWNER,
            request,
            record,
            uuid4(),
            {},
            TEMPLATE,
            {"read": "读取", "check": "复查"},
        )

    draft, roles = await adopt()
    assert roles is None
    if not independent:
        assert draft.definition.capability_declaration.promises == {
            "reconciled": "按原表核验"
        }
        assert draft.definition.capability_declaration.excludes == ("预测",)
    assert draft.definition.procedure.steps[-1].depends_on == (
        () if independent else ("read",)
    )
    registry = dict(runtime.registrations)
    runtime.registrations.clear()
    with pytest.raises(NotFoundError):
        await adopt()
    runtime.registrations.update(registry)
    from app.interfaces.endpoints.knowbot_routes import ConfirmSelection

    reviewed = ConfirmSelection(
        revision=1,
        method_id=mid,
        cases={},
        template=TEMPLATE,
        step_text={"read": "读取", "check": "复查"},
        confirmed=True,
    )
    saved = await SelectionService(repo, runtime).confirm(
        OWNER, request, record, reviewed
    )
    procedure = saved.draft_contract["procedure"]
    assert procedure["steps"][-1]["depends_on"] == ([] if independent else ["read"])
    if independent:
        assert {k: v["step"] for k, v in procedure["exports"].items()} == {
            "evidence": "read",
            "difference": "check",
        }
    receipt = (await repo.get_incubation(record.id)).decisions["selection"]
    assert receipt["revision"] == saved.revision and receipt["method_id"] == str(mid)


def test_p5_secondary_direct_candidate_must_be_executable_before_confirmation():
    from app.domain.knowbots.selection import verify_selection_graph

    source = tool()
    source["procedure"]["inputs"]["kind"] = {**slot(), "schema": {"type": "string"}}
    source["procedure"]["implementation"].update(
        kind="registered_instruction", source=str(uuid4()), entry="instruction"
    )
    source["procedure"]["max_model_turns"] = 1
    a = frozen(source)
    source["procedure"]["max_model_turns"] = 2
    b = frozen(source)
    steps = [
        step(
            "route",
            "a",
            {
                "value": {"source": "caller", "name": "rows"},
                "kind": {"source": "caller", "name": "label"},
            },
            route_input="kind",
            cases={"A": "a", "B": "b"},
        )
    ]
    draft, _ = build(steps, {"a": a, "b": b}, ["P5"], cases={"route": {"A", "B"}})
    with pytest.raises(ValueError, match="Unsupported execution contract"):
        verify_selection_graph(
            uuid4(), draft.definition, resolved(a, b), {"patterns": FLAGS}
        )


def test_p5_leaf_limit_depends_on_reachable_combinations_of_caller_inputs():
    from app.domain.knowbots.formal_method import FormalMethodDefinition
    from app.domain.knowbots.selection import verify_selection_graph
    from tests.test_formal_method import composite

    src = tool()
    slot_str = {**slot(), "schema": {"type": "string"}}
    src["procedure"]["inputs"]["value"] = slot_str
    src["procedure"]["implementation"].update(
        kind="registered_instruction", source=str(uuid4()), entry="instruction"
    )
    src["procedure"]["max_model_turns"] = 1
    leaf = frozen(src)

    def fixed_doc(children):
        doc = composite(*children)
        graph = doc["procedure"]
        graph.update(max_replans=0, max_steps=200)
        for item in graph["steps"]:
            item["on_failure"] = "fail"
            item["inputs"]["value"]["slot"] = slot_str
        return doc

    big = frozen(fixed_doc([leaf] * 51))
    doc = fixed_doc([leaf, leaf])
    for index, node in enumerate(doc["procedure"]["steps"]):
        alternatives = (leaf, big) if index == 0 else (big, leaf)
        node["candidates"] = [f.reference.model_dump(mode="json") for f in alternatives]
        node["candidate_choice"] = {
            "input": "value",
            "cases": [
                {"equals": v, "candidate": r}
                for v, r in zip("AB", node["candidates"], strict=True)
            ],
        }

    def verify():
        verify_selection_graph(
            uuid4(),
            FormalMethodDefinition.model_validate(doc),
            resolved(leaf, big),
            {"patterns": FLAGS},
        )

    # One label shared by both steps always calls 51 + 1 leaves.
    verify()
    # Independent labels can select both heavy branches: 102 leaves.
    doc["procedure"]["steps"][1]["inputs"]["value"]["binding"]["name"] = "other"
    with pytest.raises(ValueError, match="100 actual tool leaf calls"):
        verify()


@pytest.mark.parametrize("with_tree", [True, False])
def test_composite_uses_author_scope_and_root_promise(with_tree):
    from app.domain.knowbots.selection import PlanningNode, PlanningTree, SketchStep

    first = SketchStep.model_validate(
        step("n1_1", "read", {"value": {"source": "caller", "name": "facts"}})
    )
    final = SketchStep.model_validate(step("n1", "finish", {"value": output("n1_1")}))
    tree = PlanningTree(
        catalog_digest="1" * 64,
        nodes=(
            PlanningNode(
                id="n1",
                goal="交付核验",
                status="split",
                operator="sequence",
                step=final,
            ),
            PlanningNode(
                id="n1_1",
                parent="n1",
                goal="读取",
                status="hit",
                operator="leaf",
                step=first,
            ),
        ),
    )
    proposal = Selection(
        status="ready",
        reason="按已定口径核验",
        routing_required=False,
        steps=(first, final),
        patterns=("P1",),
        tree=tree if with_tree else None,
    )
    if not with_tree:
        proposal = proposal.model_copy(
            update={
                "steps": (
                    first,
                    final.model_copy(update={"deliver_as": {"result": "result"}}),
                )
            }
        )
    methods = {"read": frozen(), "finish": frozen()}
    draft, _ = expand(
        proposal,
        methods,
        TEMPLATE,
        FLAGS,
        required_cases={},
        scope=("委托人给原始披露行", "排除预测"),
    )
    decl = draft.definition.capability_declaration
    assert tuple(decl.applies_when) == ("委托人给原始披露行",)
    assert tuple(decl.excludes) == ("排除预测",)
    assert decl.promises == {"template_delivery": TEMPLATE.deliverable}
    compile_method(uuid4(), draft.definition, resolved(*methods.values()))


def test_author_delivers_evidence_and_difference_without_exposing_internal_work():
    from app.domain.knowbots.formal_method_plan import export_outputs

    source = tool()
    source["procedure"]["outputs"]["working"] = slot()
    methods = {"extract": frozen(source), "calculate": frozen()}
    steps = [
        step(
            "read",
            "extract",
            {"value": {"source": "caller", "name": "rows"}},
            deliver_as={"result": "evidence"},
        ),
        step(
            "compare",
            "calculate",
            {"value": output("read")},
            deliver_as={"result": "difference"},
        ),
    ]
    draft, _ = build(steps, methods, [])
    definition = draft.definition
    assert definition.how_to_validate[0].outputs == ("evidence", "difference")
    assert export_outputs(
        definition, {"read": {"working": 30, "result": 17}, "compare": {"result": 5}}
    ) == {
        "evidence": 17,
        "difference": 5,
    }
    for mapping, error in [
        ({"result": "evidence"}, "duplicate delivery"),
        ({"absent": "difference"}, "missing compare.absent"),
        ({}, "selection_disconnected"),
    ]:
        steps[1]["deliver_as"] = mapping
        with pytest.raises(ValueError, match=error):
            build(steps, methods, [])


def test_separate_terminal_deliveries_require_join_support():
    methods = {"read": frozen(), "calculate": frozen()}
    steps = [
        step(
            "a",
            "read",
            {"value": {"source": "caller", "name": "opening"}},
            deliver_as={"result": "evidence"},
        ),
        step(
            "b",
            "calculate",
            {"value": {"source": "caller", "name": "closing"}},
            deliver_as={"result": "difference"},
        ),
    ]
    draft, _ = build(steps, methods, [])
    assert set(draft.definition.interfaces()["inputs"]) == {"opening", "closing"}
    with pytest.raises(ValueError, match="selection_capability"):
        build(steps, methods, [], flags={**FLAGS, "join": False})


def test_single_step_tree_requires_explicit_public_output_if_tool_has_several():
    from app.domain.knowbots.selection import PlanningNode, PlanningTree, SketchStep

    call = SketchStep.model_validate(
        step("n1", "m1", {"value": {"source": "literal", "value": 1}})
    )
    leaf = PlanningNode(id="n1", goal="核验", status="hit", operator="leaf", step=call)
    plan = Selection(
        status="ready",
        reason="核验",
        routing_required=False,
        tree=PlanningTree(catalog_digest="1" * 64, nodes=(leaf,)),
        steps=(call,),
    )
    draft, _ = expand(
        plan, {"m1": frozen()}, None, FLAGS, required_cases={}, question="核验"
    )
    assert set(draft.definition.capability_declaration.promises) == {"request_delivery"}
    doc = tool()
    doc["procedure"]["outputs"]["raw_debug"] = slot()
    with pytest.raises(ValueError, match="selection_export"):
        expand(
            plan, {"m1": frozen(doc)}, None, FLAGS, required_cases={}, question="核验"
        )
