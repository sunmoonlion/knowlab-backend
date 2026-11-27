"""PR-1: Five-Element + Two-View contract, S1-S4, V0 checks & orthogonal compiler."""

from uuid import uuid4

import pytest

from app.domain.knowbots.formal_method import (
    ApprovalRule,
    CollectionCoverageCheck,
    ExpectedDeliverableSpec,
    FormalMethodDefinition,
    MethodDraftV2,
    NumericIdentityCheck,
    ProhibitedTermsCheck,
    ProjectionLens,
    ReviewComparison,
    SchemaRangeCheck,
    SourcedCitationCheck,
    ValidationRule,
    VerifiedArtifactGuard,
    build_engineering_archetype,
    build_slot_preset,
    extract_public_contract,
    project_contract_state,
    slot_meaning_compatible,
)
from app.domain.knowbots.formal_method_plan import (
    bind_step_inputs,
    evaluate_artifact_guard,
    evaluate_v0_checks,
    export_outputs,
    select_plan,
    verify_envelope_graph,
)
from app.domain.knowbots.managed_capabilities_generated import (
    AccessRequirement,
    DataTransfer,
    ResourceScope,
)
from app.domain.knowbots.selection import (
    Selection,
    SketchStep,
    expand,
    verify_selection_graph,
)
from tests.test_formal_method import composite, frozen, resolved, slot, tool

FLAGS = dict.fromkeys(
    ("sequence", "join", "independent_review", "object_batch", "conditional"), True
)


def test_v1_digest_invariance_is_preserved_bit_for_bit():
    raw = tool()
    for slots in ("inputs", "outputs"):
        for slot_spec in raw["procedure"][slots].values():
            slot_spec["meaning"].pop("domain_id", None)
    raw["procedure"]["implementation"]["kind"] = "mcp"
    definition = FormalMethodDefinition.model_validate(raw)
    assert (
        definition.digest()
        == "sha256:a7028e4372ab314bea62fc816d65a1f838a57cdd5b522c24d2b86dc37553484d"
    )


def test_slot_vocabulary_presets_and_prefix_covariance():
    amt = build_slot_preset(
        "monetary_amount",
        description="期末货币资金",
        domain_id="fin.statement.balance_sheet",
        currency="CNY",
    )
    assert amt.accepts({"amount": 120.5, "currency": "CNY"})
    assert not amt.accepts({"amount": 120.5, "currency": "CNY", "extra": 1})
    assert slot_meaning_compatible(amt.meaning, {"domain_id": "fin.statement"})
    assert not slot_meaning_compatible(amt.meaning, {"domain_id": "fin.metric"})
    assert not slot_meaning_compatible({"unit": "dimensionless"}, {"unit": "currency"})


def test_archetypes_block_unconfirmed_freeze_and_project_live_two_views():
    for arch in ("S1", "S2", "S3", "S4"):
        draft = build_engineering_archetype(arch, title=f"测试样板 {arch}")
        state = project_contract_state(draft)
        assert not state["ready_to_freeze"]
        assert not state["closure_checks"]["provenance_confirmed"]
        assert not state["closure_checks"]["procedure_present"]
        assert len(state["what_to_return"]) == 1
        with pytest.raises(ValueError, match="Unconfirmed suggestions"):
            draft.to_definition()


def test_whole_governs_part_exports_permission_namespacing_and_clean_inputs():
    req_a = AccessRequirement(
        requirement_id="conn_read",
        capability_ref="erp_api",
        version="1",
        operations=("read_ledger",),
        resources=(ResourceScope(resource_role="book", values=("book_a",)),),
        data_transfer=DataTransfer(
            destination="https://erp.example.test/mcp",
            argument_fields=("period",),
            purpose="读取账套 A 发生额",
        ),
    )
    req_b = req_a.model_copy(
        update={
            "resources": (ResourceScope(resource_role="book", values=("book_b",)),),
            "data_transfer": DataTransfer(
                destination="https://erp.example.test/mcp",
                argument_fields=("period",),
                purpose="读取账套 B 发生额",
            ),
        }
    )
    t1 = tool()
    t1["access_requirements"] = [req_a.model_dump(mode="json")]
    t1["procedure"]["implementation"]["kind"] = "mcp"
    t1["procedure"]["implementation"]["requirement_ids"] = ["conn_read"]
    t1["procedure"]["outputs"]["result"] = build_slot_preset(
        "check_receipt",
        description="核验回执",
        domain_id="fin.reconciliation.receipt",
    ).model_dump(mode="json", by_alias=True)
    t1["what_requires_approval"] = [
        ApprovalRule(
            requirement_id="conn_read",
            operations=("read_ledger",),
            when="立项确认包络授权",
            preview=("period",),
            on_denied="pause",
            scope="envelope",
        ).model_dump(mode="json")
    ]
    t2 = tool()
    t2["access_requirements"] = [req_b.model_dump(mode="json")]
    t2["procedure"]["implementation"]["kind"] = "mcp"
    t2["procedure"]["implementation"]["requirement_ids"] = ["conn_read"]
    t2["procedure"]["outputs"]["summary"] = build_slot_preset(
        "cited_markdown",
        description="附引文汇总说明",
        domain_id="core.document.cited_markdown",
    ).model_dump(mode="json", by_alias=True)
    del t2["procedure"]["outputs"]["result"]
    t2["how_to_validate"][0]["outputs"] = ["summary"]

    m1, m2 = frozen(t1), frozen(t2)
    draft_v2 = MethodDraftV2(
        archetype_id="S2",
        method_kind="expert",
        title="双账套勾稽与附注专家",
        purpose="联合导出首步勾稽回执与次步说明文稿",
        applies_when=("委托人提供会计期间",),
        excludes=("不做投资收益承诺",),
        expected_deliverables=(
            ExpectedDeliverableSpec(
                promise_id="reconcile_receipt",
                summary="交付勾稽回执",
                expected_domain_id="fin.reconciliation",
                expected_schema=t1["procedure"]["outputs"]["result"]["schema"],
            ),
            ExpectedDeliverableSpec(
                promise_id="cited_summary",
                summary="交付说明文稿",
                expected_domain_id="core.document.cited_markdown",
                expected_schema=t2["procedure"]["outputs"]["summary"]["schema"],
            ),
        ),
        validation_rules=(
            ValidationRule(
                promise_id="reconcile_receipt",
                outputs=("receipt_out",),
                check="核验勾稽回执",
                evidence="账套对账表",
                on_failure="fail",
                v0_checks=(
                    SchemaRangeCheck(kind="schema_and_range", pointer="/receipt_out"),
                ),
            ),
            ValidationRule(
                promise_id="cited_summary",
                outputs=("summary_out",),
                check="核验引文与禁用词",
                evidence="原文定位",
                on_failure="fail",
                v0_checks=(
                    SourcedCitationCheck(
                        kind="sourced_citation", pointer="/summary_out"
                    ),
                    ProhibitedTermsCheck(
                        kind="prohibited_terms", pointer="/summary_out"
                    ),
                ),
            ),
        ),
    )
    sel = Selection(
        status="ready",
        reason="分步核验并联合导出",
        routing_required=False,
        steps=(
            SketchStep(
                id="n1_1",
                task="对账",
                method="m1",
                inputs={"value": {"source": "caller", "name": "n1_1_period_num"}},
            ),
            SketchStep(
                id="n1",
                task="成稿",
                method="m2",
                inputs={"value": {"source": "literal", "value": 2026}},
            ),
        ),
    )
    expanded_draft, _ = expand(
        sel,
        {"m1": m1, "m2": m2},
        None,
        FLAGS,
        required_cases={},
        draft_contract=draft_v2,
        step_exports={
            "receipt_out": ("n1_1", "result"),
            "summary_out": ("n1", "summary"),
        },
    )
    named = sel.model_copy(
        update={
            "steps": (
                sel.steps[0].model_copy(
                    update={"deliver_as": {"result": "receipt_out"}}
                ),
                sel.steps[1].model_copy(
                    update={"deliver_as": {"summary": "summary_out"}}
                ),
            )
        }
    )
    kwargs = dict(required_cases={}, draft_contract=draft_v2)
    mapped, _ = expand(named, {"m1": m1, "m2": m2}, None, FLAGS, **kwargs)
    assert mapped.definition.digest() == expanded_draft.definition.digest()
    with pytest.raises(ValueError, match="conflicting delivery bindings"):
        expand(
            named,
            {"m1": m1, "m2": m2},
            None,
            FLAGS,
            step_exports={"receipt_out": ("n1", "summary")},
            **kwargs,
        )
    with pytest.raises(ValueError, match="outputs differ from the root contract"):
        expand(
            sel,
            {"m1": m1, "m2": m2},
            None,
            FLAGS,
            step_exports={
                "receipt_out": ("n1_1", "result"),
                "summary_out": ("n1", "summary"),
                "extra": ("n1", "summary"),
            },
            **kwargs,
        )
    defn = expanded_draft.definition
    # M02/R03: promises preserved, n1_1_ stripped, colliding req_id namespaced!
    assert set(defn.capability_declaration.promises) == {
        "reconcile_receipt",
        "cited_summary",
    }
    assert set(defn.interfaces()["inputs"]) == {"period_num"}
    assert set(defn.interfaces()["outputs"]) == {"receipt_out", "summary_out"}
    req_ids = {r.requirement_id for r in defn.access_requirements}
    assert req_ids == {"conn_read", "n1__conn_read"}

    pub = extract_public_contract(defn)
    assert pub.method_kind == "expert"
    assert {s.name for s in pub.public_inputs} == {"period_num"}
    assert {s.name for s in pub.what_to_return} == {"receipt_out", "summary_out"}
    assert set(pub.v0_check_kinds) == {
        "schema_and_range",
        "sourced_citation",
        "prohibited_terms",
    }
    assert pub.per_action_approval_count == 0


def test_projection_lens_collect_pack_artifact_guard_and_v0_evaluators():
    wide_tool = tool()
    wide_tool["procedure"]["implementation"]["kind"] = "mcp"
    wide_tool["procedure"]["outputs"]["result"] = build_slot_preset(
        "structured_record",
        description="宽表记录",
        domain_id="fin.statement.balance_sheet",
        fields={
            "assets": {"type": "number"},
            "liabilities_and_equity": {"type": "number"},
            "abs_diff": {"type": "number"},
        },
    ).model_dump(mode="json", by_alias=True)

    narrow_tool = tool()
    narrow_tool["procedure"]["implementation"]["kind"] = "mcp"
    narrow_tool["procedure"]["inputs"]["value"] = build_slot_preset(
        "structured_record",
        description="窄输入",
        domain_id="fin.statement",
        fields={"gap_amount": {"type": "number"}},
    ).model_dump(mode="json", by_alias=True)
    narrow_tool["procedure"]["outputs"]["result"] = build_slot_preset(
        "cited_markdown",
        description="深层核查附注说明",
        domain_id="fin.disclosure.note",
    ).model_dump(mode="json", by_alias=True)

    pack_tool = tool()
    pack_tool["procedure"]["implementation"]["kind"] = "mcp"
    pack_tool["procedure"]["inputs"]["value"] = {
        "description": "多路归集数组",
        "schema": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tag", "status", "value"],
                "additionalProperties": False,
                "properties": {
                    "tag": {"type": "string"},
                    "status": {"type": "string", "enum": ["present", "skipped"]},
                    "value": {
                        "anyOf": [
                            narrow_tool["procedure"]["outputs"]["result"]["schema"],
                            {"type": "null"},
                        ]
                    },
                },
            },
        },
        "required": True,
        "meaning": {"domain_id": "core.table.rows", "vocab_version": "1"},
        "on_missing": "fail",
    }
    pack_tool["procedure"]["outputs"]["result"] = wide_tool["procedure"]["outputs"][
        "result"
    ]

    f_wide, f_narrow, f_pack = frozen(wide_tool), frozen(narrow_tool), frozen(pack_tool)
    lens = ProjectionLens(
        pick_fields=("abs_diff",), rename_fields={"abs_diff": "gap_amount"}
    )
    guard = VerifiedArtifactGuard(
        step="s0", output="result", pointer="/abs_diff", op="gt", value=1.0
    )

    doc = composite(f_wide, f_narrow, f_pack)
    p = doc["procedure"]
    p.update(max_replans=0, max_depth=3, max_steps=10, max_expansions=10)
    p["steps"][0]["outputs"]["result"] = wide_tool["procedure"]["outputs"]["result"]
    p["steps"][0]["on_failure"] = "fail"
    p["steps"][1].update(
        depends_on=["s0"],
        always_required=False,
        artifact_guard=guard.model_dump(mode="json"),
        on_failure="fail",
        inputs={
            "value": {
                "slot": narrow_tool["procedure"]["inputs"]["value"],
                "binding": {
                    "source": "step",
                    "step": "s0",
                    "output": "result",
                    "lens": lens.model_dump(mode="json"),
                },
            }
        },
        outputs={"result": narrow_tool["procedure"]["outputs"]["result"]},
    )
    p["steps"][2].update(
        depends_on=["s0", "s1"],
        on_failure="fail",
        inputs={
            "value": {
                "slot": pack_tool["procedure"]["inputs"]["value"],
                "binding": {
                    "source": "collect_pack",
                    "items": [
                        {
                            "tag": "deep_note",
                            "step": "s1",
                            "output": "result",
                            "allow_skipped": True,
                        }
                    ],
                },
            }
        },
        outputs={"result": wide_tool["procedure"]["outputs"]["result"]},
    )
    p["exports"] = {
        "result": {"step": "s2", "output": "result", "always_present": True}
    }
    doc["how_to_validate"] = [
        ValidationRule(
            promise_id="value",
            outputs=("result",),
            check="资产等于负债加权益",
            evidence="报表平衡恒等式",
            on_failure="fail",
            v0_checks=(
                NumericIdentityCheck(
                    kind="numeric_identity",
                    lhs_pointer="/outputs/result/assets",
                    rhs_pointer="/outputs/result/liabilities_and_equity",
                    tolerance=0.01,
                ),
            ),
        ).model_dump(mode="json")
    ]
    defn = FormalMethodDefinition.model_validate(doc)
    deps = resolved(f_wide, f_narrow, f_pack)
    depth, expansions, leaves = verify_envelope_graph(uuid4(), defn, deps)
    assert (depth, expansions, leaves) == (2, 4, 3)

    # Case A: abs_diff == 0.0 -> guard False -> s1 skipped -> CollectPack skipped!
    out_s0_clean = {
        "result": {"assets": 100.0, "liabilities_and_equity": 100.0, "abs_diff": 0.0}
    }
    assert not evaluate_artifact_guard(guard, {"s0": out_s0_clean})
    bound_s2 = bind_step_inputs(
        defn.procedure.steps[2], {}, {"s0": out_s0_clean}, skipped_steps={"s1"}
    )
    assert bound_s2["value"] == [
        {"tag": "deep_note", "status": "skipped", "value": None}
    ]
    exported = export_outputs(
        defn, {"s0": out_s0_clean, "s2": out_s0_clean}, skipped_steps={"s1"}
    )
    v0_ok = evaluate_v0_checks(defn, {}, exported)
    assert len(v0_ok) == 1 and v0_ok[0].passed

    # Case B: abs_diff == 5.0 -> guard True -> ProjectionLens renames gap_amount!
    out_s0_diff = {
        "result": {"assets": 105.0, "liabilities_and_equity": 100.0, "abs_diff": 5.0}
    }
    assert evaluate_artifact_guard(guard, {"s0": out_s0_diff})
    bound_s1 = bind_step_inputs(defn.procedure.steps[1], {}, {"s0": out_s0_diff})
    assert bound_s1 == {"value": {"gap_amount": 5.0}}
    v0_fail = evaluate_v0_checks(defn, {}, {"result": out_s0_diff["result"]})
    assert not v0_fail[0].passed and "exceeds tolerance" in v0_fail[0].detail

    # Coverage & prohibited terms V0 checks
    cov_doc = tool()
    cov_doc["how_to_validate"][0]["v0_checks"] = [
        CollectionCoverageCheck(
            kind="collection_coverage",
            items_pointer="/result",
            key_field="id",
            expected_keys_input_slot="entities",
        ).model_dump(mode="json"),
        ProhibitedTermsCheck(
            kind="prohibited_terms",
            pointer="/result",
        ).model_dump(mode="json"),
    ]
    cov_def = FormalMethodDefinition.model_validate(cov_doc)
    checks = evaluate_v0_checks(
        cov_def,
        {"entities": ["A", "B"]},
        {"result": [{"id": "A", "note": "客观核对"}, {"id": "B", "note": "建议买入"}]},
    )
    assert checks[0].passed is True
    assert checks[1].passed is False and "建议买入" in checks[1].detail


def test_local_strict_plan_and_step_batch_compose_inside_multi_step_dag():
    # 4-step graph: extract -> producer -> reviewer -> pack
    extract_doc = tool()
    extract_doc["procedure"]["implementation"]["kind"] = "mcp"
    f_extract = frozen(extract_doc)

    prod_doc = tool()
    prod_doc["procedure"]["implementation"].update(
        kind="registered_instruction", source=str(uuid4()), entry="instruction"
    )
    prod_doc["procedure"]["max_model_turns"] = 1
    prod_doc["procedure"]["outputs"] = {
        "result": slot(),
        "self_check": slot(),
    }
    prod_doc["how_to_validate"][0]["outputs"] = ["result", "self_check"]
    f_prod = frozen(prod_doc)

    rev_doc = tool()
    rev_doc["procedure"]["implementation"].update(
        kind="registered_instruction", source=str(uuid4()), entry="instruction"
    )
    rev_doc["procedure"]["max_model_turns"] = 1
    rev_doc["procedure"]["inputs"] = {"draft": slot(), "self_check": slot()}
    f_rev = frozen(rev_doc)

    raw = composite(f_extract, f_prod, f_rev, f_extract)
    p = raw["procedure"]
    p.update(max_replans=0, max_depth=3, max_steps=20, max_expansions=20)
    for s in p["steps"]:
        s["on_failure"] = "fail"
    p["steps"][1].update(
        depends_on=["s0"],
        inputs={
            "value": {
                "slot": slot(),
                "binding": {"source": "step", "step": "s0", "output": "result"},
            }
        },
        outputs={"result": slot(), "self_check": slot()},
    )
    p["steps"][2].update(
        depends_on=["s1"],
        inputs={
            "draft": {
                "slot": slot(),
                "binding": {"source": "step", "step": "s1", "output": "result"},
            },
            "self_check": {
                "slot": slot(),
                "binding": {"source": "step", "step": "s1", "output": "self_check"},
            },
        },
    )
    p["steps"][3].update(
        depends_on=["s2"],
        inputs={
            "value": {
                "slot": slot(),
                "binding": {"source": "step", "step": "s2", "output": "result"},
            }
        },
    )
    p["exports"] = {
        "result": {"step": "s3", "output": "result", "always_present": True}
    }
    p["collaboration"] = {
        "s1": {"isolation_group": "writer"},
        "s2": {
            "isolation_group": "reviewer",
            "handoff_from": ["s1"],
            "review_requirements": {"s1": "isolated_reviewer"},
            "comparison": ReviewComparison(
                assessment_output="self_check", max_reworks=1
            ).model_dump(mode="json"),
        },
    }
    defn = FormalMethodDefinition.model_validate(raw)
    expanded_rework = select_plan(defn, {"value": 1}, revisions=1)
    step_ids = [s.id for s in expanded_rework.procedure.steps]
    # s0 stays before review subgraph, s3 stays after and rewires to comparison1!
    assert step_ids[0] == "s0" and step_ids[-1] == "s3"
    assert len(step_ids) == 1 + 6 + 1
    assert expanded_rework.procedure.steps[-1].depends_on[0].startswith("review_")
    verify_selection_graph(
        uuid4(),
        defn,
        resolved(f_extract, f_prod, f_rev),
        {"patterns": FLAGS},
    )


def test_b03_two_independent_deliverables_identify_missing_promise_source():
    t_table = tool()
    t_table["procedure"]["outputs"] = {
        "rows": build_slot_preset(
            "structured_record",
            description="科目明细表",
            domain_id="core.table.rows",
            fields={"items": {"type": "array"}},
        ).model_dump(mode="json", by_alias=True)
    }
    t_table["how_to_validate"][0]["outputs"] = ["rows"]

    t_check = tool()
    t_check["procedure"]["outputs"] = {
        "receipt": build_slot_preset(
            "check_receipt",
            description="勾稽核对结果",
            domain_id="core.check.receipt",
        ).model_dump(mode="json", by_alias=True)
    }
    t_check["how_to_validate"][0]["outputs"] = ["receipt"]

    f_table, f_check = frozen(t_table), frozen(t_check)
    doc = composite(f_table, f_check)
    doc["procedure"]["steps"][0]["outputs"] = {
        "rows": t_table["procedure"]["outputs"]["rows"]
    }
    doc["procedure"]["steps"][1]["outputs"] = {
        "receipt": t_check["procedure"]["outputs"]["receipt"]
    }
    doc["procedure"]["exports"] = {
        "detail_table": {"step": "s0", "output": "rows", "always_present": True}
    }

    partial_draft = MethodDraftV2(
        archetype_id="S1",
        method_kind="expert",
        title="双独立交付核验专家",
        purpose="输出科目明细表与勾稽核对结果",
        applies_when=("提供期间报表数据",),
        excludes=("不做投资买卖评级",),
        expected_deliverables=(
            ExpectedDeliverableSpec(
                promise_id="detail_table",
                summary="交付科目明细表",
                expected_domain_id="core.table.rows",
                expected_schema=t_table["procedure"]["outputs"]["rows"]["schema"],
            ),
            ExpectedDeliverableSpec(
                promise_id="reconcile_check",
                summary="交付勾稽核对结果",
                expected_domain_id="core.check.receipt",
                expected_schema=t_check["procedure"]["outputs"]["receipt"]["schema"],
            ),
        ),
        procedure=doc["procedure"],
        validation_rules=(
            ValidationRule(
                promise_id="detail_table",
                outputs=("detail_table",),
                check="明细表非空",
                evidence="明细行",
                on_failure="fail",
                v0_checks=(
                    SchemaRangeCheck(kind="schema_and_range", pointer="/detail_table"),
                ),
            ),
            ValidationRule(
                promise_id="reconcile_check",
                outputs=("reconcile_check",),
                check="勾稽核对回执结构完整",
                evidence="核对回执",
                on_failure="fail",
                v0_checks=(
                    SchemaRangeCheck(
                        kind="schema_and_range", pointer="/reconcile_check"
                    ),
                ),
            ),
        ),
        unconfirmed_paths=(),
    )
    partial_state = project_contract_state(partial_draft)
    assert partial_state["ready_to_freeze"] is False
    assert partial_state["closure_checks"]["deliverables_aligned"] is False
    assert partial_state["missing_promise_sources"] == ["reconcile_check"]

    doc_full = {
        **doc["procedure"],
        "exports": {
            "detail_table": {"step": "s0", "output": "rows", "always_present": True},
            "reconcile_check": {
                "step": "s1",
                "output": "receipt",
                "always_present": True,
            },
        },
    }
    complete_draft = MethodDraftV2.model_validate(
        {**partial_draft.model_dump(mode="json", by_alias=True), "procedure": doc_full}
    )
    complete_state = project_contract_state(complete_draft)
    assert complete_state["missing_promise_sources"] == []
    assert complete_state["closure_checks"]["deliverables_aligned"] is True
    assert complete_state["ready_to_freeze"] is True
