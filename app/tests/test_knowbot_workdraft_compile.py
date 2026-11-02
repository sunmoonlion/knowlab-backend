"""工作稿编清单、拆回、改格子、核出处（专家孵化方案第 7.2、7.3、9、10 节）：纯函数。"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.domain.knowbots.compile import (
    DEFAULT_INPUT_MAX_CHARS,
    DEFAULT_RESERVE,
    DEFAULT_TOOLS,
    PLATFORM_HEAD,
    PLATFORM_TAIL,
    V2_TOOLS,
    compile_manifest,
    decompile,
    pack_problems,
)
from app.domain.knowbots.drafts import draft_manifest_problems
from app.domain.knowbots.workdraft import (
    WorkdraftBody,
    apply,
    bundle_text,
    canonical,
    cells,
    empty_body,
    origins,
    source_problems,
)
from tests.knowbot_fakes import manifest as foreign_manifest

OWNER = "user-123"
WD = uuid.UUID(int=7)


def body(**over: Any) -> dict[str, Any]:
    """两步：第 2 步用上第 1 步，退回第 1 步。"""
    doc: dict[str, Any] = {
        "name": "财务分析与预测",
        "tagline": "按我的方法分析财报",
        "scope": {"does": "A 股非金融公司的财务分析", "does_not": "银行"},
        "steps": [
            {
                "id": "overview",
                "title": "数据摸底",
                "does": "看清数据有哪些表、哪些报告期。\n单位与合并口径也要看。",
                "why": "先知道手上有什么。",
                "rules": [
                    {"id": "r1", "text": "合并、母公司两种都有时，问用户用哪一种。"},
                    {"id": "r2", "text": "单位统一换成元。"},
                ],
                "ask_before": [
                    {"id": "basis", "question": "用哪种报表？", "options": ["合并"]}
                ],
                "outputs": [
                    {
                        "name": "periods",
                        "type": "list",
                        "items": [{"name": "period", "type": "text"}],
                    },
                    {"name": "unit", "type": "text"},
                ],
                "checks": [{"template": "list_min", "params": {"path": "periods"}}],
            },
            {
                "id": "margins",
                "title": "算毛利率",
                "does": "按我的口径算每期毛利率。",
                "uses": ["overview"],
                "rules": [{"id": "r1", "text": "毛利率 = (收入 - 成本) / 收入。"}],
                "outputs": [{"name": "margins", "type": "list"}],
                "checks": [{"template": "computed", "params": {"path": "margins"}}],
                "on_fail": {"kind": "back", "back_to": "overview"},
            },
        ],
    }
    doc.update(over)
    return doc


def sourced(doc: dict[str, Any]) -> tuple[WorkdraftBody, dict[str, Any]]:
    parsed = WorkdraftBody.model_validate(doc)
    return parsed, {
        p: {"kind": "user_text", "ref": f"record:{uuid.UUID(int=i + 100)}"}
        for i, p in enumerate(cells(parsed))
    }


def build(doc: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[Any]]:
    parsed, sources = sourced(doc or body())
    return compile_manifest(
        parsed,
        sources,
        pack_id="knowlab-x",
        version="2",
        runtime="knowbench",
        fallback_name="申请时的名字",
    )


def with_check(template: str, params: dict[str, Any]) -> dict[str, Any]:
    doc = body()
    doc["steps"][1]["outputs"] = [
        {"name": "margins", "type": "list", "items": [{"name": "period"}]},
        {"name": "note", "type": "text"},
    ]
    doc["steps"][1]["checks"] = [{"template": template, "params": params}]
    return doc


# ---------- 编清单 ----------
def test_the_compiled_manifest_passes_the_draft_check_and_the_pack_check() -> None:
    manifest, problems = build()
    assert problems == []
    assert draft_manifest_problems(manifest, runtime="knowbench", version="2") == []
    assert pack_problems(manifest["pack"]) == []
    assert manifest["pack"]["scope"]["does"] == "A 股非金融公司的财务分析"
    assert manifest["pack"]["title"] == manifest["name"] == "财务分析与预测"
    assert "bindings" not in manifest and "data_needs" not in manifest


def test_method_text_is_platform_text_plus_the_authors_words_verbatim() -> None:
    manifest, _ = build()
    first, second = manifest["pack"]["workflow"]
    text = first["method_text"]
    assert text.startswith(PLATFORM_HEAD) and text.endswith(PLATFORM_TAIL)
    # 作者原文逐字（含换行），规则按顺序编号
    assert "这一步：看清数据有哪些表、哪些报告期。\n单位与合并口径也要看。" in text
    assert "1. 合并、母公司两种都有时，问用户用哪一种。\n2. 单位统一换成元。" in text
    assert "- periods：列表，每项有：period（文字）" in text
    assert "- unit：文字" in text
    assert "用上：overview（数据摸底）" in second["method_text"]
    # 「做什么」也是给用户看的一句话
    assert first["summary"] == body()["steps"][0]["does"]
    assert first["why"] == "先知道手上有什么。"


def test_engineering_defaults_follow_knowbench() -> None:
    manifest, _ = build()
    first, second = manifest["pack"]["workflow"]
    assert first["tools"] == list(DEFAULT_TOOLS)
    # 跑的途中也能再要数据、查主体（请专家方案第 4.3 节第 7 条）
    assert set(DEFAULT_TOOLS) == V2_TOOLS
    assert {"request_data", "find_subject", "compute"} <= set(DEFAULT_TOOLS)
    assert "search_evidence" in DEFAULT_TOOLS  # 查引文
    assert (first["reserve"], first["input_max_chars"]) == (
        DEFAULT_RESERVE,
        DEFAULT_INPUT_MAX_CHARS,
    )
    assert (first["on_reject"], first["max_reworks"]) == ("rework", 1)
    assert (second["on_reject"], second["back_to"]) == ("back", "overview")
    assert second["input_refs"] == ["overview"]
    assert first["output_artifact"] == "overview"
    assert first["ask_before"] == [
        {
            "id": "basis",
            "question": "用哪种报表？",
            "options": [{"id": "o1", "label": "合并"}],
        }
    ]


def test_every_step_carries_json_object_required_keys_and_no_advice() -> None:
    manifest, _ = build()
    for step in manifest["pack"]["workflow"]:
        kinds = [r["kind"] for r in step["acceptance"]]
        assert kinds[0] == "json_object" and kinds[-1] == "no_positioning_advice"
        required = next(r for r in step["acceptance"] if r["kind"] == "required_keys")
        assert required["keys"] == list(step["output_schema"])


def test_output_schema_follows_outputs_and_what_the_checks_need() -> None:
    doc = body()
    doc["steps"][0]["outputs"].append({"name": "revenue", "type": "number"})
    doc["steps"][0]["checks"].append(
        {"template": "sourced", "params": {"path": "revenue"}}
    )
    manifest, problems = build(doc)
    assert problems == []
    first, second = manifest["pack"]["workflow"]
    assert first["output_schema"] == {
        "periods": [{"period": "string"}],
        "unit": "string",
        "revenue": {"value": "number", "address": "string"},
    }
    assert second["output_schema"] == {
        "margins": [{"value": "number", "compute_id": "string"}]
    }
    assert "compute_id 是 compute 返回的编号" in second["method_text"]


@pytest.mark.parametrize(
    ("template", "params", "expected"),
    [
        ("required", {"keys": ["note"]}, {"kind": "required_keys", "keys": ["note"]}),
        ("non_empty", {"path": "note"}, {"kind": "non_empty", "path": "note"}),
        (
            "list_min",
            {"path": "margins", "min": 3},
            {"kind": "list_min", "path": "margins", "min_items": 3},
        ),
        ("list_empty", {"path": "margins"}, {"kind": "list_empty", "path": "margins"}),
        ("blank", {"path": "note"}, {"kind": "blank", "path": "note"}),
        (
            "each_has",
            {"path": "margins", "keys": ["period"], "field": "period", "values": ["Q"]},
            {
                "kind": "each_has",
                "path": "margins",
                "keys": ["period"],
                "field": "period",
                "values": ["Q"],
            },
        ),
        (
            "all_equal",
            {"path": "margins", "field": "period", "equals": "2025"},
            {
                "kind": "all_equal",
                "path": "margins",
                "field": "period",
                "equals": "2025",
            },
        ),
        (
            "computed",
            {"path": "margins", "expression": "(a - b) / a", "tolerance": 0.001},
            {
                "kind": "computed",
                "path": "margins",
                "expression": "(a - b) / a",
                "tolerance": 0.001,
            },
        ),
        (
            "identity",
            {"path": "margins", "tolerance": 1},
            {"kind": "identity", "path": "margins", "tolerance": 1},
        ),
        ("sourced", {"path": "margins"}, {"kind": "sourced", "path": "margins"}),
        (
            "quoted",
            {"path": "margins", "field": "period", "equals": "支持"},
            {"kind": "quoted", "path": "margins", "field": "period", "equals": "支持"},
        ),
        (
            "covers",
            {
                "path": "margins",
                "field": "period",
                "source_step": "overview",
                "source_path": "periods",
                "source_field": "period",
                "also": ["note"],
            },
            {
                "kind": "covers",
                "path": "margins",
                "field": "period",
                "source": "overview.periods",
                "source_field": "period",
                "also": ["note"],
            },
        ),
        ("max_chars", {"max": 3000}, {"kind": "max_chars", "max_chars": 3000}),
    ],
)
def test_each_check_template_becomes_an_acceptance_rule(
    template: str, params: dict[str, Any], expected: dict[str, Any]
) -> None:
    doc = with_check(template, params)
    if template == "covers":
        doc["steps"][1]["outputs"][1] = {
            "name": "note",
            "type": "list",
            "items": [{"name": "period"}],
        }
    manifest, problems = build(doc)
    assert problems == []
    rules = manifest["pack"]["workflow"][1]["acceptance"]
    made = [r for r in rules if r["kind"] == expected["kind"]][-1]
    assert {k: made[k] for k in expected} == expected
    assert made["label"] and made["message"].startswith("没过：")
    assert pack_problems(manifest["pack"]) == []
    # 验收也进 rule_sources，带出处
    assert any("怎样算对" in s["rule"] for s in manifest["rule_sources"])


@pytest.mark.parametrize(
    ("template", "params", "why"),
    [
        ("non_empty", {}, "needs path"),
        ("non_empty", {"path": "nope"}, "not an output"),
        ("non_empty", {"path": "note", "min": 1}, "does not take min"),
        ("required", {"keys": ["other"]}, "outputs of this step"),
        ("list_min", {"path": "margins", "min": 0}, "from 1"),
        ("each_has", {"path": "margins"}, "keys or values"),
        ("each_has", {"path": "margins", "values": ["a"]}, "field they limit"),
        ("all_equal", {"path": "margins", "field": "x", "equals": [1]}, "plain"),
        ("computed", {"path": "margins", "tolerance": -1}, "not below 0"),
        ("quoted", {"path": "margins", "field": "x"}, "go together"),
        (
            "covers",
            {
                "path": "margins",
                "field": "p",
                "source_step": "margins",
                "source_path": "margins",
            },
            "earlier step",
        ),
        (
            "covers",
            {
                "path": "margins",
                "field": "p",
                "source_step": "overview",
                "source_path": "nope",
            },
            "not an output of step overview",
        ),
        ("max_chars", {"max": 0}, "from 1"),
    ],
)
def test_bad_check_params_are_named_by_the_cell(
    template: str, params: dict[str, Any], why: str
) -> None:
    _, problems = build(with_check(template, params))
    assert [p.path for p in problems] == ["steps/margins/checks/0"]
    assert why in problems[0].message


def test_what_cannot_be_compiled_is_named_by_the_cell() -> None:
    doc = body(scope={"does": ""})
    doc["steps"][1]["uses"] = ["later"]
    doc["steps"][1]["title"] = ""
    doc["steps"][0]["outputs"] = []
    doc["steps"][0]["checks"] = []
    doc["steps"][0]["on_fail"] = {"kind": "back", "back_to": "margins"}
    doc["steps"][0]["ask_before"][0]["question"] = " "
    doc["steps"][1]["advanced"] = {"tools": ["shell"]}
    _, problems = build(doc)
    assert {p.path for p in problems} == {
        "scope/does",
        "steps/margins/uses",
        "steps/margins/title",
        "steps/overview/outputs",
        "steps/overview/on_fail",
        "steps/overview/ask_before/basis",
        "steps/margins/advanced",
    }
    _, none = build(body(steps=[]))
    assert [p.path for p in none] == ["steps"]


def test_advanced_overrides_the_generated_engineering() -> None:
    doc = body()
    doc["steps"][0]["advanced"] = {
        "tools": ["read_input"],
        "reserve": "0.2",
        "input_max_chars": 24000,
        "output_schema": {"periods": ["string"]},
        "output_file": "底稿/摸底.md",
    }
    manifest, problems = build(doc)
    assert problems == []
    first = manifest["pack"]["workflow"][0]
    assert first["tools"] == ["read_input"]
    assert (first["reserve"], first["input_max_chars"]) == ("0.2", 24000)
    assert first["output_schema"] == {"periods": ["string"]}
    assert first["output_file"] == "底稿/摸底.md"


def test_rule_sources_gather_rules_and_checks_with_their_refs() -> None:
    parsed, sources = sourced(body())
    manifest, _ = compile_manifest(
        parsed, sources, pack_id="p", version="1", runtime="knowbench", fallback_name=""
    )
    refs = {s["ref"] for s in manifest["rule_sources"]}
    assert refs == {
        sources[p]["ref"]
        for p in (
            "steps/overview/rules/r1",
            "steps/overview/rules/r2",
            "steps/overview/checks/0",
            "steps/margins/rules/r1",
            "steps/margins/checks/0",
        )
    }
    assert all(len(s["rule"]) <= 200 for s in manifest["rule_sources"])
    # 没写名称一格时用申请时起的名字
    unnamed, _ = build(body(name=""))
    assert unnamed["name"] == "申请时的名字"


def test_pack_problems_mirror_the_runtime_constraints() -> None:
    manifest, _ = build()
    pack = copy.deepcopy(manifest["pack"])
    step = pack["workflow"][1]
    step["acceptance"] += [
        {"kind": "sourced"},
        {"kind": "covers", "path": "margins", "field": "p", "source": "nodot"},
        {"kind": "list_min", "values": ["a"]},
        {"kind": "non_empty", "expression": "a"},
        {"kind": "made_up"},
        {"kind": "covers", "path": "m", "field": "p", "source": "later.x"},
    ]
    step["tools"] = ["shell"]
    step["back_to"] = "nowhere"
    step["input_refs"] = ["nowhere"]
    step["surprise"] = 1
    pack["workflow"][0]["output_file"] = "../out.md"
    found = {(p.path, p.message) for p in pack_problems(pack)}
    where = "pack.workflow[1]"
    assert (f"{where}.acceptance[4]", "sourced needs a path") in found
    assert (
        f"{where}.acceptance[5]",
        "covers needs a field and a source artifact.path",
    ) in found
    assert (f"{where}.acceptance[6]", "only each_has with a field limits values") in (
        found
    )
    assert (f"{where}.acceptance[7]", "only computed takes an expression") in found
    assert (f"{where}.acceptance[8]", "unknown kind 'made_up'") in found
    assert (f"{where}.acceptance[9]", "covers must read an earlier artifact") in found
    assert (f"{where}.tools", "a knowbot cannot use these") in found
    assert (f"{where}.back_to", "must be an earlier step") in found
    assert (f"{where}.input_refs", "must name earlier artifacts") in found
    assert (f"{where}.surprise", "unknown field") in found
    assert ("pack.workflow[0].output_file", "must be a plain relative path") in found
    assert pack_problems({"pack_id": "p"})[-1].path == "pack.workflow"


# ---------- 拆回（导入、复制） ----------
def test_our_own_manifest_comes_back_cell_for_cell() -> None:
    manifest, _ = build()
    back, notes = decompile(manifest)
    assert notes == []
    assert back.model_dump() == WorkdraftBody.model_validate(body()).model_dump()


def test_a_foreign_pack_keeps_its_method_text_as_what_the_step_does() -> None:
    doc = foreign_manifest()
    doc["pack"]["workflow"] = [
        {
            "step_id": "s1",
            "title": "读财报",
            "summary": "读三张表",
            "method_text": "Read the statements. Reply with ONE JSON object.",
            "tools": ["read_input", "compute"],
            "output_artifact": "summary",
            "output_schema": {
                "gross_margin": {"value": "number", "basis": "string"},
                "rows": [{"k": "string"}],
            },
            "acceptance": [
                {"kind": "json_object"},
                {"kind": "required_keys", "keys": ["gross_margin"]},
                {"kind": "no_positioning_advice", "path": "rows"},
            ],
            "reserve": "0.1",
            "input_max_chars": 24000,
            "max_backs": 2,
        },
        {
            "step_id": "s2",
            "title": "下结论",
            "method_text": "Conclude.",
            "input_refs": ["summary", "ghost"],
            "output_artifact": "end",
            "output_schema": {"rows": [{"k": "string"}]},
            "acceptance": [
                {
                    "kind": "covers",
                    "path": "rows",
                    "field": "k",
                    "source": "summary.rows",
                    "source_field": "k",
                }
            ],
            "on_reject": "human",
        },
    ]
    back, notes = decompile(doc)
    s1, s2 = back.steps
    assert s1.does == "Read the statements. Reply with ONE JSON object."
    assert s1.rules == ()
    assert [(o.name, o.type) for o in s1.outputs] == [
        ("gross_margin", "number"),
        ("rows", "list"),
    ]
    # 工程层照导入的留进「高级」
    assert s1.advanced.tools == ("read_input", "compute")
    assert (s1.advanced.reserve, s1.advanced.input_max_chars) == ("0.1", 24000)
    assert s1.advanced.output_schema == {
        "gross_margin": {"value": "number", "basis": "string"},
        "rows": [{"k": "string"}],
    }
    # 「必须有」只剩作者选的那条；不在默认里的措辞检查记下来
    assert [c.template for c in s1.checks] == ["required"]
    assert s2.uses == ("s1",) and s2.on_fail.kind == "ask"
    assert s2.checks[0].params["source_step"] == "s1"
    assert any("max_backs" in n for n in notes)
    assert any("no_positioning_advice" in n for n in notes)
    assert any("input_refs" in n for n in notes)
    # 拆回的可以再编（covers 的上游改成了步骤编号）
    parsed, sources = sourced(back.model_dump(mode="json") | {"scope": {"does": "x"}})
    again, problems = compile_manifest(
        parsed, sources, pack_id="p", version="1", runtime="knowbench", fallback_name=""
    )
    assert problems == []
    assert again["pack"]["workflow"][1]["acceptance"][2]["source"] == "s1.rows"
    assert pack_problems(again["pack"]) == []


def test_missing_data_is_neither_compiled_checked_in_nor_imported() -> None:
    """「缺数据的需求」随 knowbench 那一套删了（decisions 10-29「发版前补齐」①）：
    编出的清单不带 missing_data，草稿检查当不认识的字段，导入也不收（⑰）。"""
    doc, problems = build()
    assert problems == []
    assert all("missing_data" not in step for step in doc["pack"]["workflow"])
    doc["pack"]["workflow"][0]["missing_data"] = "ask"
    assert any(
        p.path.endswith(".missing_data") and p.message == "unknown field"
        for p in pack_problems(doc["pack"])
    )
    with pytest.raises(ValueError, match="missing_data"):
        decompile(doc)


def test_a_method_text_that_does_not_parse_back_stays_whole() -> None:
    manifest, _ = build()
    text = manifest["pack"]["workflow"][0]["method_text"]
    manifest["pack"]["workflow"][0]["method_text"] = text.replace(
        PLATFORM_TAIL, "Reply in JSON."
    )
    back, _ = decompile(manifest)
    assert back.steps[0].does == manifest["pack"]["workflow"][0]["method_text"]
    assert back.steps[0].rules == ()


def test_only_a_v2_manifest_with_steps_is_imported() -> None:
    for bad in (
        [],
        {"schema": "knowbot-manifest/v1", "pack": {"workflow": [{}]}},
        foreign_manifest(pack={"pack_id": "p", "workflow": []}),
        foreign_manifest(pack={"pack_id": "p", "workflow": [{"step_id": "1 a"}]}),
    ):
        with pytest.raises(ValueError):
            decompile(bad)


# ---------- 改格子 ----------
def test_apply_edits_cells_and_blocks() -> None:
    doc = body()
    doc = apply(doc, "scope/elsewhere", "去别处问")
    doc = apply(doc, "steps/overview/rules/r3", "第三条")
    doc = apply(doc, "steps/overview/rules/r1", None)
    doc = apply(doc, "steps/margins/checks/1", {"template": "blank", "params": {}})
    doc = apply(doc, "steps/margins/checks/0", None)
    doc = apply(doc, "steps/extra", {"title": "加一步"})
    doc = apply(doc, "steps/overview/outputs/unit", {"type": "number"})
    parsed = WorkdraftBody.model_validate(doc)
    assert parsed.scope.elsewhere == "去别处问"
    assert [r.id for r in parsed.steps[0].rules] == ["r2", "r3"]
    assert [c.template for c in parsed.steps[1].checks] == ["blank"]
    assert parsed.steps[2].id == "extra"
    assert parsed.steps[0].outputs[1].type == "number"
    removed = WorkdraftBody.model_validate(apply(doc, "steps/extra", None))
    assert [s.id for s in removed.steps] == ["overview", "margins"]
    for path, value in (
        ("", "x"),
        ("nope", "x"),
        ("scope/who", "x"),
        ("steps/ghost/title", "x"),
        ("steps/overview/secret", "x"),
        ("steps/overview/checks/9", {}),
        ("steps/overview/rules/r9", None),
        ("steps/overview/outputs/unit", "text"),
    ):
        with pytest.raises(ValueError):
            apply(doc, path, value)


def test_cells_are_the_method_layer_with_words() -> None:
    parsed = WorkdraftBody.model_validate(body())
    found = cells(parsed)
    assert (
        found["steps/overview/rules/r1"] == "合并、母公司两种都有时，问用户用哪一种。"
    )
    assert json.loads(found["steps/overview/checks/0"]) == {
        "template": "list_min",
        "params": {"path": "periods"},
    }
    assert "scope/elsewhere" not in found  # 空的不算
    assert "steps/margins/uses" not in found  # 结构不要出处
    assert cells(WorkdraftBody.model_validate(empty_body())) == {}


# ---------- 出处 ----------
@dataclass(frozen=True)
class Entry:
    id: uuid.UUID
    workdraft_id: uuid.UUID
    kind: str
    actor_subject: str
    actor_role: str
    path: str | None
    text: str


def _entries(parsed: WorkdraftBody) -> tuple[dict[str, Any], dict[uuid.UUID, Entry]]:
    sources: dict[str, Any] = {}
    entries: dict[uuid.UUID, Entry] = {}
    for i, (path, text) in enumerate(cells(parsed).items()):
        rid = uuid.UUID(int=1000 + i)
        entries[rid] = Entry(rid, WD, "edit", OWNER, "author", path, text)
        sources[path] = {"kind": "user_text", "ref": f"record:{rid}"}
    return sources, entries


def _check(parsed, sources, entries, answered=()):  # type: ignore[no-untyped-def]
    return source_problems(
        parsed,
        sources,
        workdraft_id=WD,
        owner_subject=OWNER,
        entries=entries,
        answered=answered,
    )


def test_every_cell_with_a_matching_record_passes() -> None:
    parsed = WorkdraftBody.model_validate(body())
    sources, entries = _entries(parsed)
    assert _check(parsed, sources, entries) == []


def test_sources_that_do_not_hold_are_named_by_the_cell() -> None:
    parsed = WorkdraftBody.model_validate(body())
    sources, entries = _entries(parsed)
    rule, does, title, why = (
        "steps/overview/rules/r1",
        "steps/overview/does",
        "steps/overview/title",
        "steps/overview/why",
    )
    del sources["name"]  # 没有出处
    sources["tagline"] = {"kind": "user_text", "ref": f"record:{uuid.uuid4()}"}
    one = entries[uuid.UUID(sources[rule]["ref"][7:])]
    entries[one.id] = Entry(one.id, WD, "edit", OWNER, "author", rule, one.text + "。")
    two = entries[uuid.UUID(sources[does]["ref"][7:])]
    entries[two.id] = Entry(
        two.id, uuid.uuid4(), "edit", OWNER, "author", does, two.text
    )
    three = entries[uuid.UUID(sources[title]["ref"][7:])]
    entries[three.id] = Entry(
        three.id, WD, "edit", "someone", "author", title, "数据摸底"
    )
    four = entries[uuid.UUID(sources[why]["ref"][7:])]
    entries[four.id] = Entry(four.id, WD, "edit", OWNER, "operator", why, four.text)
    sources["scope/does"] = {"kind": "user_text", "ref": "第 4 问"}
    sources["scope/does_not"]["kind"] = "user_confirmed"  # 编辑记录不能当确认
    found = {p.path: p.message for p in _check(parsed, sources, entries)}
    assert found == {
        "name": "this cell has no source",
        "tagline": "the source record does not exist",
        rule: "the cell differs from its record",
        does: "the source record is another workdraft's",
        title: "the source record is not the author's",
        why: "the source record is not the author's",
        "scope/does": "the source is neither a record nor a question",
        "scope/does_not": "a edit record cannot be cited as user_confirmed",
    }


def test_origins_name_how_each_cell_came() -> None:
    """页面在格子旁标的来路：引的记录是哪种，答过的缺口是 question，引不上的不列。"""
    edit = Entry(uuid.UUID(int=1), WD, "edit", OWNER, "author", "name", "x")
    copy = Entry(uuid.UUID(int=2), WD, "copy", OWNER, "author", None, "{}")
    sources = {
        "name": {"kind": "user_text", "ref": f"record:{edit.id}"},
        "tagline": {"kind": "user_text", "ref": f"record:{copy.id}"},
        "scope/does": {"kind": "user_confirmed", "ref": f"question:{uuid.uuid4()}"},
        "scope/does_not": {"kind": "user_text", "ref": f"record:{uuid.uuid4()}"},
        "scope/elsewhere": {"kind": "user_text", "ref": "第 4 问"},
    }
    assert origins(sources, {edit.id: edit, copy.id: copy}) == {
        "name": "edit",
        "tagline": "copy",
        "scope/does": "question",
    }


def test_a_record_for_another_cell_does_not_hold() -> None:
    parsed = WorkdraftBody.model_validate(body())
    sources, entries = _entries(parsed)
    sources["steps/overview/rules/r2"] = sources["steps/overview/rules/r1"]
    found = _check(parsed, sources, entries)
    assert [(p.path, p.message) for p in found] == [
        ("steps/overview/rules/r2", "the cell differs from its record")
    ]


def test_import_records_hold_cell_by_cell_and_accepts_are_confirmed() -> None:
    parsed = WorkdraftBody.model_validate(body())
    found = cells(parsed)
    bundle = Entry(uuid.UUID(int=1), WD, "import", OWNER, "author", None, "")
    bundle = Entry(
        bundle.id, WD, "import", OWNER, "author", None, bundle_text("import", found)
    )
    sources = {p: {"kind": "user_text", "ref": f"record:{bundle.id}"} for p in found}
    accept = Entry(
        uuid.UUID(int=2), WD, "accept", OWNER, "author", "name", "财务分析与预测"
    )
    sources["name"] = {"kind": "user_confirmed", "ref": f"record:{accept.id}"}
    entries = {bundle.id: bundle, accept.id: accept}
    assert _check(parsed, sources, entries) == []
    # 导入后又改了字、没有新记录：核不上
    changed = WorkdraftBody.model_validate(body(tagline="改过的介绍"))
    assert [p.path for p in _check(changed, sources, entries)] == ["tagline"]
    sources["name"]["kind"] = "user_text"  # 接受记录只能当确认
    assert [p.path for p in _check(parsed, sources, entries)] == ["name"]


def test_an_answered_question_is_a_confirmed_source() -> None:
    parsed = WorkdraftBody.model_validate(body())
    sources, entries = _entries(parsed)
    question = str(uuid.uuid4())
    sources["name"] = {"kind": "user_confirmed", "ref": f"question:{question}"}
    assert _check(parsed, sources, entries, {question}) == []
    assert [p.message for p in _check(parsed, sources, entries)] == [
        "the source is not a question the author answered"
    ]
    sources["name"]["kind"] = "user_text"
    assert [p.message for p in _check(parsed, sources, entries, {question})] == [
        "an answered question is user_confirmed"
    ]


def test_structured_cells_compare_by_their_canonical_text() -> None:
    parsed = WorkdraftBody.model_validate(body())
    path = "steps/overview/outputs/periods"
    assert cells(parsed)[path] == canonical(
        {
            "name": "periods",
            "type": "list",
            "items": [{"name": "period", "type": "text"}],
        }
    )
