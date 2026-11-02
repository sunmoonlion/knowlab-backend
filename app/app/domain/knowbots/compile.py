"""由工作稿生成清单 v2（专家孵化方案第 7.2、7.3 节，decisions ⑮）。纯函数。

作者只写方法层；工程层由这里按格子生成：

- 每一步的 `method_text` 按第 7.3 节的模板拼：平台固定文字 + 作者原文（逐字，不改写、
  不翻译）+ 按交回项生成的说明。模型在运行时看到的每一句，要么是作者确认过的，要么是
  平台对所有专家都一样的固定文字（C-L1 由程序核得了）。
- `output_schema` 按交回项生成；交回项自动带「必须有」（`required_keys`）；每一步固定带
  `json_object` 与 `no_positioning_advice`；作者选的验收模板编成运行方的验收条目
  （knowbench `AcceptanceRule`）。
- 工具默认给运行方能给专家的全部：只读读数据（含查引文）、计算、要数据、查主体（跑的途中
  可以再要数据、查主体，请专家方案第 4.3 节第 7 条）；额度、读入上限用运行方的默认值；
  「高级」里可以改（删掉不要的工具）。
- `rule_sources` 由规则与验收汇成，每条带出处；工程层不进（同 0029 第二节）。

对得上的运行方（knowbench）：`app/domain/workbench/packs.py` 的 `StepContract`、
`AcceptanceRule`、`ExpertPack`，`app/domain/workbench/knowbots.py` 的 `V2_TOOLS`。这里不
引它们（不同仓），出版本前用 `pack_problems` 照那几条约束再核一遍。

导入（第十节）是反过来：`decompile` 把一份清单 v2 拆回格子，方法层能还原的还原，还原
不了的整段方法原文放进「做什么」；工程层留进「高级」。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.domain.knowbots.drafts import DYNAMIC_MANIFEST_SCHEMA, MANIFEST_SCHEMA
from app.domain.knowbots.workdraft import (
    NAME,
    STEP_ID,
    Advanced,
    CheckCell,
    MethodSelection,
    OutputCell,
    PlanningCell,
    Problem,
    StepCell,
    WorkdraftBody,
)

# ---------- 平台固定文字（所有专家共用；改它是平台的新版本，不算改某个专家） ----------
PLATFORM_HEAD = (
    "你在按一位作者写定的方法做其中一步。方法是作者的：照下面写的做，不加自己的判断，"
    "不替作者补口径、阈值、预测方法。只用这一步给你的工具读数据；每个数只能来自数据"
    "或上游的交回物，不凭记忆、不自己编。不给买卖建议、评级、目标价。"
)
PLATFORM_TAIL = (
    "只交回一个 JSON 对象，前后不加说明、不加代码块标记；交回项缺了或类型不对，这一步"
    "不算过。这一步不改任何文件。调用工具时照工具说明带上这件委托的委托号。"
)
STEP_LINE = "这一步："
RULES_LINE = "规则："
USES_LINE = "用上："
OUTPUTS_LINE = "交回一个 JSON，包含："

# 与 knowbench `V2_TOOLS` 一致：分身能用的只有 knowbase 数据 MCP 的读工具、要数据、
# 查主体、计算
DATA_MCP_TOOLS = frozenset(
    {
        "list_inputs",
        "read_input",
        "query_facts",
        "query_metric",
        "read_facts",
        "describe_items",
        "search_evidence",
        "lookup",
    }
)
V2_TOOLS = DATA_MCP_TOOLS | {
    "compute",
    "request_data",
    "find_subject",
    "discover_source_tools",
}
# 默认给的：全部——只读读数据（含查引文 search_evidence）、计算、要数据、查主体。专家跑的
# 途中也可以再要数据、查主体（请专家方案第 4.3 节第 7 条），不只第 0 步；「高级」里能删
DEFAULT_TOOLS: tuple[str, ...] = tuple(sorted(V2_TOOLS - {"discover_source_tools"}))
# 与 knowbench `StepContract` 的默认值一致
DEFAULT_RESERVE = "0.05"
DEFAULT_INPUT_MAX_CHARS = 8000
DEFAULT_MAX_REWORKS = 1  # 所有者 10-04 定：重做 1 次，再不过停下来问
RULE_LABEL_CHARS = 200  # knowmart RuleSource.rule 的上限

TYPE_WORDS = {"number": "数", "text": "文字", "list": "列表", "table": "表"}
FIELD_WORDS = {"number": "数", "text": "文字"}
SCALAR_SCHEMA = {"number": "number", "text": "string"}
# 数值类、引文验收要交回物每项带的几项（knowbench acceptance_numbers）
EXTRA_SCHEMA: dict[str, dict[str, Any]] = {
    "computed": {"value": "number", "compute_id": "string"},
    "identity": {"left_id": "string", "right_id": "string"},
    "sourced": {"address": "string", "value": "number"},
    "quoted": {
        "quotes": [{"material_id": "string", "page": "integer", "text": "string"}]
    },
}
EXTRA_WORDS = {
    "computed": "每个数写成 {value, compute_id}：value 是数，compute_id 是 compute 返回"
    "的编号",
    "identity": "每项写成 {left_id, right_id}：要相等的两次 compute 的编号",
    "sourced": "每个数写成 {address, value}：address 是数据那一格的编号（fact:…），"
    "value 是那个数",
    "quoted": "每项带 quotes：一串 {material_id, page, text}，原文照读到的抄",
}
# 只认得数、文字时用的值（covers 的 also、each_has 的 values …）
Scalar = str | int | float | bool

# 每个验收模板收哪些参数（必填的、可选的）
TEMPLATES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "required": (frozenset({"keys"}), frozenset()),
    "non_empty": (frozenset({"path"}), frozenset()),
    "list_min": (frozenset({"path"}), frozenset({"min"})),
    "list_empty": (frozenset({"path"}), frozenset()),
    "blank": (frozenset({"path"}), frozenset()),
    "each_has": (frozenset({"path"}), frozenset({"keys", "field", "values"})),
    "all_equal": (frozenset({"path", "field", "equals"}), frozenset()),
    "computed": (frozenset({"path"}), frozenset({"expression", "tolerance"})),
    "identity": (frozenset({"path"}), frozenset({"tolerance"})),
    "sourced": (frozenset({"path"}), frozenset({"tolerance"})),
    "quoted": (frozenset({"path"}), frozenset({"field", "equals"})),
    "covers": (
        frozenset({"path", "field", "source_step", "source_path"}),
        frozenset({"source_field", "also"}),
    ),
    "max_chars": (frozenset({"max"}), frozenset()),
}
# 模板 → 运行方的验收种类（名字不同的几个）
KIND_OF = {"required": "required_keys", "max_chars": "max_chars"}
NUMERIC_RULES = frozenset({"computed", "identity", "sourced", "quoted", "covers"})
ACCEPTANCE_KINDS = frozenset(
    {
        "json_object",
        "required_keys",
        "non_empty",
        "list_min",
        "no_positioning_advice",
        "max_chars",
        "all_equal",
        "list_empty",
        "blank",
        *NUMERIC_RULES,
        "each_has",
    }
)
_NAME = re.compile(NAME)
_STEP = re.compile(STEP_ID)


def artifact_of(step_id: str) -> str:
    """这一步的交回物名就是步骤编号（covers 引上游时写「步骤编号.路径」）。"""
    return step_id


def _label(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= RULE_LABEL_CHARS else text[: RULE_LABEL_CHARS - 1] + "…"


def _is_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool)


def _words(names: list[str] | tuple[str, ...]) -> str:
    return "、".join(f"「{n}」" for n in names)


# ---------- 验收模板 ----------
def check_rule(
    check: CheckCell, step: StepCell, earlier: Mapping[str, StepCell]
) -> tuple[dict[str, Any] | None, str | None]:
    """一条验收模板编成运行方的验收条目：（条目，不行的原因）。earlier：前面各步。"""
    required, optional = TEMPLATES[check.template]
    params = check.params
    missing = sorted(required - set(params))
    unknown = sorted(set(params) - required - optional)
    if missing:
        return None, f"{check.template} needs {', '.join(missing)}"
    if unknown:
        return None, f"{check.template} does not take {', '.join(unknown)}"
    outputs = {o.name for o in step.outputs}
    rule: dict[str, Any] = {"kind": KIND_OF.get(check.template, check.template)}
    path = params.get("path")
    if "path" in required:
        if not isinstance(path, str) or not path:
            return None, "path must be the name of an output"
        if path.split(".")[0] not in outputs:
            return None, f"path {path} is not an output of this step"
        rule["path"] = path
    tolerance = params.get("tolerance", 0)
    if "tolerance" in params:
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, int | float)
            or tolerance < 0
        ):
            return None, "tolerance must be a number not below 0"
        rule["tolerance"] = tolerance
    t = check.template
    if t == "required":
        keys = params["keys"]
        if (
            not isinstance(keys, list)
            or not keys
            or any(not isinstance(k, str) or not k for k in keys)
        ):
            return None, "keys must be a non-empty list of names"
        if not set(keys) <= outputs:
            return None, "keys must be outputs of this step"
        rule["keys"] = list(keys)
        label = f"必须有：{_words(keys)}"
    elif t == "non_empty":
        label = f"「{path}」不能空"
    elif t == "list_min":
        least = params.get("min", 1)
        if isinstance(least, bool) or not isinstance(least, int) or least < 1:
            return None, "min must be a whole number from 1"
        rule["min_items"] = least
        label = f"「{path}」至少 {least} 项"
    elif t == "list_empty":
        label = f"「{path}」必须是空的"
    elif t == "blank":
        label = f"「{path}」留空"
    elif t == "each_has":
        keys = params.get("keys", [])
        field = params.get("field")
        values = params.get("values", [])
        if not isinstance(keys, list) or any(
            not isinstance(k, str) or not k for k in keys
        ):
            return None, "keys must be a list of names"
        if not isinstance(values, list) or any(not _is_scalar(v) for v in values):
            return None, "values must be a list of plain values"
        if values and (not isinstance(field, str) or not field):
            return None, "values need the field they limit"
        if field is not None and (not isinstance(field, str) or not field):
            return None, "field must be a name"
        if not keys and not values:
            return None, "each_has needs keys or values"
        rule |= {"keys": list(keys)}
        if field and values:
            rule |= {"field": field, "values": list(values)}
        label = f"「{path}」每项都有{_words(keys)}" if keys else f"「{path}」每项"
        if field and values:
            label += f"，「{field}」只能是{_words([str(v) for v in values])}"
    elif t == "all_equal":
        field, equals = params["field"], params["equals"]
        if not isinstance(field, str) or not field:
            return None, "field must be a name"
        if not _is_scalar(equals):
            return None, "equals must be a plain value"
        rule |= {"field": field, "equals": equals}
        label = f"「{path}」每项的「{field}」都是 {equals}"
    elif t == "computed":
        expression = params.get("expression")
        if expression is not None:
            if not isinstance(expression, str) or not expression.strip():
                return None, "expression must be a formula"
            rule["expression"] = expression
        label = f"「{path}」的数都是用计算工具算的" + (
            f"（算式 {expression}）" if expression else ""
        )
    elif t == "identity":
        label = f"「{path}」两边相等" + (f"（容差 {tolerance}）" if tolerance else "")
    elif t == "sourced":
        label = f"「{path}」的数和原数据那一格对得上"
    elif t == "quoted":
        field, equals = params.get("field"), params.get("equals")
        if (field is None) != (equals is None):
            return None, "field and equals go together"
        if field is not None:
            if not isinstance(field, str) or not field or not _is_scalar(equals):
                return None, "field must be a name and equals a plain value"
            rule |= {"field": field, "equals": equals}
        label = f"「{path}」的说法附原文引文"
    elif t == "covers":
        field, upstream = params["field"], params["source_step"]
        source_path, source_field = params["source_path"], params.get("source_field")
        also = params.get("also", [])
        if not isinstance(field, str) or not field:
            return None, "field must be a name"
        if not isinstance(upstream, str) or upstream not in earlier:
            return None, "source_step must be an earlier step"
        if not isinstance(source_path, str) or source_path.split(".")[0] not in {
            o.name for o in earlier[upstream].outputs
        }:
            return None, f"source_path is not an output of step {upstream}"
        if source_field is not None and (
            not isinstance(source_field, str) or not source_field
        ):
            return None, "source_field must be a name"
        if not isinstance(also, list) or any(
            not isinstance(a, str) or a.split(".")[0] not in outputs for a in also
        ):
            return None, "also must list outputs of this step"
        rule |= {"field": field, "source": f"{artifact_of(upstream)}.{source_path}"}
        if source_field:
            rule["source_field"] = source_field
        if also:
            rule["also"] = list(also)
        label = f"「{path}」与第「{earlier[upstream].title or upstream}」步一一对上"
    else:  # max_chars
        most = params["max"]
        if (
            isinstance(most, bool)
            or not isinstance(most, int)
            or not 1 <= most <= 200_000
        ):
            return None, "max must be a whole number from 1 to 200000"
        rule["max_chars"] = most
        label = f"不超过 {most} 字"
    rule |= {"label": label, "message": f"没过：{label}"}
    return rule, None


# ---------- 交回物 ----------
def extras_of(step: StepCell) -> dict[str, list[str]]:
    """交回项 → 它的验收要每项多带的几样（按模板，顺序照验收）。"""
    found: dict[str, list[str]] = {}
    for check in step.checks:
        path = check.params.get("path")
        if check.template in EXTRA_SCHEMA and isinstance(path, str):
            kinds = found.setdefault(path, [])
            if check.template not in kinds:
                kinds.append(check.template)
    return found


def output_schema(step: StepCell) -> dict[str, Any]:
    """按交回项生成给模型看的结构（数值类、引文验收要的几样一起带上）。"""
    extras = extras_of(step)
    schema: dict[str, Any] = {}
    for output in step.outputs:
        more: dict[str, Any] = {}
        for kind in extras.get(output.name, []):
            more |= EXTRA_SCHEMA[kind]
        fields = {f.name: SCALAR_SCHEMA[f.type] for f in output.items or ()}
        if output.type in ("number", "text"):
            base = "value" if output.type == "number" else "text"
            schema[output.name] = (
                {base: SCALAR_SCHEMA[output.type], **more}
                if more
                else SCALAR_SCHEMA[output.type]
            )
        else:
            item = {**fields, **more}
            schema[output.name] = [item] if item else ["string"]
    return schema


def describe(output: OutputCell, extras: list[str]) -> str:
    """交回项的一行说明（拼进方法原文）。"""
    line = f"- {output.name}：{TYPE_WORDS[output.type]}"
    if output.items:
        each = "每行有" if output.type == "table" else "每项有"
        line += f"，{each}：" + "、".join(
            f"{f.name}（{FIELD_WORDS[f.type]}）" for f in output.items
        )
    for kind in extras:
        line += f"；{EXTRA_WORDS[kind]}"
    return line


def method_text(step: StepCell, titles: Mapping[str, str]) -> str:
    """第 7.3 节的模板：平台固定文字 + 作者原文逐字 + 按交回项生成的说明。"""
    parts = [PLATFORM_HEAD, "", STEP_LINE + step.does]
    rules = [r for r in step.rules if r.text.strip()]
    if rules:
        parts += ["", RULES_LINE, *(f"{i}. {r.text}" for i, r in enumerate(rules, 1))]
    if step.uses:
        parts += [
            "",
            USES_LINE
            + "、".join(
                f"{artifact_of(u)}（{titles[u]}）" if titles.get(u) else artifact_of(u)
                for u in step.uses
            ),
        ]
    extras = extras_of(step)
    parts += [
        "",
        OUTPUTS_LINE,
        *(describe(o, extras.get(o.name, [])) for o in step.outputs),
        "",
        PLATFORM_TAIL,
    ]
    return "\n".join(parts)


# ---------- 整份 ----------
def compile_manifest(
    body: WorkdraftBody,
    sources: Mapping[str, Any],
    *,
    pack_id: str,
    version: str,
    runtime: str,
    fallback_name: str,
    method_sources: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Compile a new author workdraft and diagnose unsatisfiable coverage rules.

    Historical snapshot replay uses the unchanged v1 translation below; new
    freeze, bind, test and delivery operations must use this authoring entry.
    """
    manifest, problems = _compile_manifest(
        body,
        sources,
        pack_id=pack_id,
        version=version,
        runtime=runtime,
        fallback_name=fallback_name,
        method_sources=method_sources,
    )
    return manifest, [*problems, *_coverage_problems(body)]


def _shape_at(schema: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(schema, dict) or part not in schema:
            return None
        schema = schema[part]
    return schema


def _coverage_shape_problem(
    rule: dict[str, Any], own: dict[str, Any], upstream: dict[str, Any]
) -> str | None:
    # These are the compiler's output descriptors, not JSON Schema. Traverse
    # only object keys, just as runtime value_at does (no array indices).
    source, _, path = rule["source"].partition(".")
    shape = _shape_at(upstream.get(source), path)
    if not isinstance(shape, list) or len(shape) != 1:
        return "covers source must declare a list with one item schema"
    wanted = shape[0]
    source_field = rule.get("source_field")
    if source_field:
        if not isinstance(wanted, dict) or source_field not in wanted:
            return "covers source_field must exist on each source item"
        wanted = wanted[source_field]
    for path in (rule["path"], *rule.get("also", [])):
        shape = _shape_at(own, path)
        item = shape[0] if isinstance(shape, list) and len(shape) == 1 else shape
        if not isinstance(item, dict) or rule["field"] not in item:
            return f"covers {path} must declare objects containing {rule['field']}"
        actual = item[rule["field"]]
        numeric = (
            isinstance(actual, str)
            and isinstance(wanted, str)
            and actual in ("number", "integer")
            and wanted in ("number", "integer")
        )
        if actual != wanted and not numeric:
            return f"covers {path}.{rule['field']} must match the source value type"
    return None


def _coverage_problems(body: WorkdraftBody) -> list[Problem]:
    schemas = {
        s.id: s.advanced.output_schema
        if s.advanced.output_schema is not None
        else output_schema(s)
        for s in body.steps
    }
    available = {s.id: s for s in body.steps}
    earlier: dict[str, StepCell] = {}
    problems = []
    for step in body.steps:
        for index, check in enumerate(step.checks):
            if check.template != "covers":
                continue
            rule, _ = check_rule(check, step, available if body.planning else earlier)
            if rule is not None:
                why = _coverage_shape_problem(rule, schemas[step.id], schemas)
                if why:
                    problems.append(Problem(f"steps/{step.id}/checks/{index}", why))
        earlier[step.id] = step
    return problems


def _compile_manifest(
    body: WorkdraftBody,
    sources: Mapping[str, Any],
    *,
    pack_id: str,
    version: str,
    runtime: str,
    fallback_name: str,
    method_sources: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """工作稿 → 清单 v2 与编不出来的地方（按格子路径）。

    出处够不够、核不核得上不在这里看（workdraft.source_problems）；有出处的规则与验收
    汇进 rule_sources。fallback_name：名称一格没写时用申请时起的名字。
    """
    problems: list[Problem] = []
    if body.method_refs or method_sources:
        from app.domain.knowbots.method_composition import validate_sources

        try:
            validate_sources(body, method_sources or {})
        except (ValueError, StopIteration):
            problems.append(
                Problem("method_refs", "method reference or projection is invalid")
            )
    name = body.name.strip() or fallback_name
    if not body.scope.does.strip():
        problems.append(Problem("scope/does", "say what this expert does"))
    if not body.steps:
        problems.append(Problem("steps", "add at least one step"))
    earlier: dict[str, StepCell] = {}
    dynamic = body.planning is not None
    available = {s.id: s for s in body.steps}
    workflow: list[dict[str, Any]] = []
    rule_sources: list[dict[str, Any]] = []
    titles = {s.id: s.title for s in body.steps}
    for step in body.steps:
        base = f"steps/{step.id}"
        inputs = (
            {key: value for key, value in available.items() if key != step.id}
            if dynamic
            else earlier
        )
        if dynamic != (step.selection is not None):
            problems.append(
                Problem(
                    f"{base}/selection",
                    "selection is required only for dynamic methods",
                )
            )
        step_name = step.title or step.id
        if not step.title.strip():
            problems.append(Problem(f"{base}/title", "name this step"))
        if not step.does.strip():
            problems.append(Problem(f"{base}/does", "say what this step does"))
        if not step.outputs:
            problems.append(Problem(f"{base}/outputs", "add at least one output"))
        for used in step.uses:
            if used not in inputs:
                problems.append(
                    Problem(f"{base}/uses", f"{used} is not an available input method")
                )
        for ask in step.ask_before:
            if not ask.question.strip():
                problems.append(
                    Problem(f"{base}/ask_before/{ask.id}", "write the question")
                )
        if step.on_fail.kind == "back" and step.on_fail.back_to not in inputs:
            problems.append(Problem(f"{base}/on_fail", "go back to an earlier step"))
        advanced = step.advanced
        if advanced.tools is not None and not set(advanced.tools) <= V2_TOOLS:
            problems.append(
                Problem(
                    f"{base}/advanced",
                    "tools must be among " + ", ".join(sorted(V2_TOOLS)),
                )
            )
        acceptance: list[dict[str, Any]] = [
            {
                "kind": "json_object",
                "label": "交回的是一个 JSON",
                "message": "交回物不是 JSON 对象",
            }
        ]
        if step.outputs:
            keys = [o.name for o in step.outputs]
            acceptance.append(
                {
                    "kind": "required_keys",
                    "keys": keys,
                    "label": f"交回项都在：{_words(keys)}",
                    "message": "缺交回项",
                }
            )
        for index, check in enumerate(step.checks):
            rule, why = check_rule(check, step, inputs)
            path = f"{base}/checks/{index}"
            if rule is None:
                problems.append(Problem(path, why or "invalid check"))
                continue
            acceptance.append(rule)
            source = sources.get(path)
            if isinstance(source, dict):
                rule_sources.append(
                    {
                        "rule": _label(f"{step_name}·怎样算对：{rule['label']}"),
                        "source": source.get("kind"),
                        "ref": source.get("ref"),
                    }
                )
        acceptance.append(
            {
                "kind": "no_positioning_advice",
                "label": "没有评级、目标价、买卖建议",
                "message": "含有评级、目标价或买卖建议的措辞",
            }
        )
        for rule in step.rules:
            source = sources.get(f"{base}/rules/{rule.id}")
            if rule.text.strip() and isinstance(source, dict):
                rule_sources.append(
                    {
                        "rule": _label(f"{step_name}：{rule.text}"),
                        "source": source.get("kind"),
                        "ref": source.get("ref"),
                    }
                )
        on_reject = {"rework": "rework", "back": "back", "ask": "human"}
        contract: dict[str, Any] = {
            "step_id": step.id,
            "title": step.title,
            "summary": step.does,
            "why": step.why,
            "input_refs": [artifact_of(u) for u in step.uses],
            "method_text": method_text(step, titles),
            "tools": list(DEFAULT_TOOLS if advanced.tools is None else advanced.tools),
            "output_artifact": artifact_of(step.id),
            "output_schema": advanced.output_schema
            if advanced.output_schema is not None
            else output_schema(step),
            "acceptance": acceptance,
            "on_reject": on_reject[step.on_fail.kind],
            "max_reworks": DEFAULT_MAX_REWORKS,
            "reserve": advanced.reserve or DEFAULT_RESERVE,
            "input_max_chars": advanced.input_max_chars or DEFAULT_INPUT_MAX_CHARS,
            "ask_before": [
                {
                    "id": a.id,
                    "question": a.question,
                    "options": [
                        {"id": f"o{i}", "label": label}
                        for i, label in enumerate(a.options, 1)
                    ],
                }
                for a in step.ask_before
            ],
        }
        if step.on_fail.kind == "back":
            contract["back_to"] = step.on_fail.back_to
        if advanced.output_file:
            contract["output_file"] = advanced.output_file
        if body.method_refs and step.id in body.method_refs:
            contract["step_version"] = str(body.method_refs[step.id].version)
        workflow.append(contract)
        earlier[step.id] = step
    scope = {
        "does": body.scope.does,
        "does_not": body.scope.does_not,
        "elsewhere": body.scope.elsewhere,
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": name,
        "tagline": body.tagline,
        "version": version,
        "runtime": runtime,
        "pack": {
            "pack_id": pack_id,
            "version": version,
            "title": name,
            "name": name,
            "tagline": body.tagline,
            "scope": scope,
            "workflow": workflow,
        },
        "rule_sources": rule_sources,
    }
    if body.planning is not None:
        manifest["schema"] = DYNAMIC_MANIFEST_SCHEMA
        manifest["pack"].pop("workflow")
        manifest["pack"].update(
            {
                "runtime_contract": "knowbench/dynamic-methods/v1",
                "planning": body.planning.model_dump(mode="json"),
                "method_units": [
                    {
                        "selection": step.selection.model_dump(mode="json")
                        if step.selection
                        else {},
                        "contract": contract,
                    }
                    for step, contract in zip(body.steps, workflow, strict=True)
                ],
            }
        )
        if body.method_refs:
            manifest["pack"]["runtime_contract"] = "knowbench/dynamic-methods/v2"
            manifest["pack"]["method_compiler"] = "knowlab/workdraft-compiler/v1"
            manifest["pack"]["method_sources"] = method_sources or {}
        # 规划方法、条件、必经检查及权限与作者编辑/接受记录使用同一出处链。
        for path, source in sources.items():
            if (
                path == "planning"
                or path.startswith("method_refs/")
                or path.endswith(("/selection", "/uses", "/advanced", "/on_fail"))
            ):
                if isinstance(source, dict):
                    rule_sources.append(
                        {
                            "rule": _label(path),
                            "source": source.get("kind"),
                            "ref": source.get("ref"),
                        }
                    )
    return manifest, problems


# ---------- 出版本前再核一遍 pack（照运行方的约束） ----------
STEP_KEYS = frozenset(
    {
        "step_id",
        "step_version",
        "title",
        "summary",
        "why",
        "input_refs",
        "method_text",
        "tools",
        "output_artifact",
        "output_schema",
        "acceptance",
        "on_reject",
        "back_to",
        "max_reworks",
        "max_backs",
        "mode",
        "reserve",
        "input_max_chars",
        "tool_policy",
        "ask_before",
        "output_file",
    }
)
RULE_KEYS = frozenset(
    {
        "kind",
        "keys",
        "path",
        "min_items",
        "max_chars",
        "field",
        "equals",
        "values",
        "source",
        "source_field",
        "also",
        "tolerance",
        "expression",
        "message",
        "label",
    }
)


def _rule_problems(rule: Any) -> list[str]:
    """knowbench `AcceptanceRule` 的约束。"""
    if not isinstance(rule, dict):
        return ["is not an object"]
    kind = rule.get("kind")
    if kind not in ACCEPTANCE_KINDS:
        return [f"unknown kind {kind!r}"]
    problems = [f"unknown field {k}" for k in sorted(set(rule) - RULE_KEYS)]
    if kind in NUMERIC_RULES and not rule.get("path"):
        problems.append(f"{kind} needs a path")
    if rule.get("expression") is not None and kind != "computed":
        problems.append("only computed takes an expression")
    if kind == "each_has" and not rule.get("path"):
        problems.append("each_has needs a path")
    if rule.get("values") and (kind != "each_has" or not rule.get("field")):
        problems.append("only each_has with a field limits values")
    source = rule.get("source")
    if kind == "covers" and not (
        rule.get("field") and isinstance(source, str) and "." in source
    ):
        problems.append("covers needs a field and a source artifact.path")
    if (source or rule.get("source_field") or rule.get("also")) and kind != "covers":
        problems.append("only covers reads an upstream artifact")
    tolerance = rule.get("tolerance", 0)
    if isinstance(tolerance, bool) or not isinstance(tolerance, int | float):
        problems.append("tolerance must be a number")
    elif tolerance < 0:
        problems.append("tolerance must not be below 0")
    return problems


def _safe_file(path: str) -> bool:
    if not path or len(path) > 256 or "\\" in path or "\x00" in path:
        return False
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def pack_problems(pack: object) -> list[Problem]:
    """pack 结构自检（照 knowbench 专家包的形状与约束）：字段齐、类型对、引用对得上。"""
    if not isinstance(pack, dict):
        return [Problem("pack", "pack must be an object")]
    if "runtime_contract" in pack or "method_units" in pack or "planning" in pack:
        return _dynamic_pack_problems(pack)
    problems: list[Problem] = []
    for key in ("pack_id", "version", "title"):
        if not isinstance(pack.get(key), str) or not pack[key]:
            problems.append(Problem(f"pack.{key}", "is required"))
    scope = pack.get("scope")
    if scope is not None and (
        not isinstance(scope, dict) or not isinstance(scope.get("does"), str)
    ):
        problems.append(Problem("pack.scope", "scope needs does"))
    workflow = pack.get("workflow")
    if not isinstance(workflow, list) or not workflow:
        return [*problems, Problem("pack.workflow", "must be a non-empty list")]
    seen: list[str] = []
    artifacts: list[str] = []
    for index, step in enumerate(workflow):
        where = f"pack.workflow[{index}]"
        if not isinstance(step, dict):
            problems.append(Problem(where, "is not an object"))
            continue
        for key in sorted(set(step) - STEP_KEYS):
            problems.append(Problem(f"{where}.{key}", "unknown field"))
        for key in ("step_id", "title", "method_text", "output_artifact"):
            if not isinstance(step.get(key), str) or not step[key]:
                problems.append(Problem(f"{where}.{key}", "is required"))
        sid = step.get("step_id")
        if sid in seen:
            problems.append(Problem(f"{where}.step_id", "step ids must be unique"))
        refs = step.get("input_refs", [])
        if not isinstance(refs, list) or any(r not in artifacts for r in refs):
            problems.append(
                Problem(f"{where}.input_refs", "must name earlier artifacts")
            )
        tools = step.get("tools", [])
        if not isinstance(tools, list) or not set(tools) <= V2_TOOLS:
            problems.append(Problem(f"{where}.tools", "a knowbot cannot use these"))
        if not isinstance(step.get("output_schema", {}), dict):
            problems.append(Problem(f"{where}.output_schema", "must be an object"))
        on_reject = step.get("on_reject", "rework")
        if on_reject not in ("rework", "back", "human"):
            problems.append(Problem(f"{where}.on_reject", "rework, back or human"))
        if on_reject == "back" and step.get("back_to") not in seen:
            problems.append(Problem(f"{where}.back_to", "must be an earlier step"))
        for key in ("max_reworks", "input_max_chars"):
            value = step.get(key, 1)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(Problem(f"{where}.{key}", "must be a whole number"))
        if not isinstance(step.get("reserve", ""), str):
            problems.append(Problem(f"{where}.reserve", "must be a string"))
        output_file = step.get("output_file")
        if output_file is not None and not (
            isinstance(output_file, str) and _safe_file(output_file)
        ):
            problems.append(
                Problem(f"{where}.output_file", "must be a plain relative path")
            )
        asks = step.get("ask_before", [])
        if not isinstance(asks, list) or any(
            not isinstance(a, dict)
            or not isinstance(a.get("id"), str)
            or not isinstance(a.get("question"), str)
            or not isinstance(a.get("options", []), list)
            for a in asks
        ):
            problems.append(Problem(f"{where}.ask_before", "id and question each"))
        rules = step.get("acceptance", [])
        if not isinstance(rules, list):
            problems.append(Problem(f"{where}.acceptance", "must be a list"))
            rules = []
        for number, rule in enumerate(rules):
            for message in _rule_problems(rule):
                problems.append(Problem(f"{where}.acceptance[{number}]", message))
            source = rule.get("source") if isinstance(rule, dict) else None
            if (
                isinstance(source, str)
                and "." in source
                and source.split(".")[0] not in artifacts
            ):
                problems.append(
                    Problem(
                        f"{where}.acceptance[{number}]",
                        "covers must read an earlier artifact",
                    )
                )
        if isinstance(sid, str):
            seen.append(sid)
        artifact = step.get("output_artifact")
        if isinstance(artifact, str):
            artifacts.append(artifact)
    return problems


def _dynamic_pack_problems(pack: dict[str, Any]) -> list[Problem]:
    """作者侧的早期诊断；运行方仍按自己的严格契约再核一遍。"""
    if pack.get("runtime_contract") == "knowbench/dynamic-methods/v2":
        from app.domain.knowbots.method_composition import restore

        try:
            restore(
                {
                    "schema": DYNAMIC_MANIFEST_SCHEMA,
                    "pack": pack,
                    "name": pack.get("name", pack.get("title", "")),
                    "tagline": pack.get("tagline", ""),
                }
            )
        except (ValueError, KeyError, TypeError, StopIteration):
            return [
                Problem("pack.method_sources", "invalid referenced method compilation")
            ]
        return []
    problems: list[Problem] = []
    keys = {
        "runtime_contract",
        "pack_id",
        "version",
        "title",
        "name",
        "tagline",
        "scope",
        "planning",
        "method_units",
    }
    for key in sorted(set(pack) - keys):
        problems.append(Problem(f"pack.{key}", "unknown dynamic catalog field"))
    if pack.get("runtime_contract") != "knowbench/dynamic-methods/v1":
        problems.append(
            Problem("pack.runtime_contract", "unsupported runtime contract")
        )
    try:
        planning = PlanningCell.model_validate(pack.get("planning"))
    except ValidationError:
        return [*problems, Problem("pack.planning", "invalid planning contract")]
    raw_units = pack.get("method_units")
    if not isinstance(raw_units, list) or not 1 <= len(raw_units) <= 30:
        return [*problems, Problem("pack.method_units", "requires 1 to 30 methods")]
    units: dict[str, dict[str, Any]] = {}
    required = set(planning.final_units)
    for index, unit in enumerate(raw_units):
        where = f"pack.method_units[{index}]"
        if not isinstance(unit, dict) or set(unit) != {"selection", "contract"}:
            problems.append(Problem(where, "selection and contract are required"))
            continue
        try:
            selection = MethodSelection.model_validate(unit["selection"])
        except ValidationError:
            problems.append(Problem(f"{where}.selection", "invalid selection"))
            continue
        contract = unit["contract"]
        sid = contract.get("step_id") if isinstance(contract, dict) else None
        if not isinstance(sid, str) or not re.fullmatch(STEP_ID, sid) or sid in units:
            problems.append(Problem(where, "invalid or duplicate method id"))
            continue
        units[sid] = contract
        if selection.required:
            required.add(sid)
        if contract.get("output_artifact") != sid:
            problems.append(Problem(where, "output slot must equal method id"))
        refs = contract.get("input_refs", [])
        if (
            not isinstance(refs, list)
            or any(not isinstance(r, str) for r in refs)
            or len(set(refs)) != len(refs)
        ):
            problems.append(Problem(where, "input slots must be unique names"))
            continue
        if contract.get("on_reject") == "back" and contract.get("back_to") not in refs:
            problems.append(Problem(where, "back_to must name an input slot"))
        if contract.get("on_reject") != "back" and contract.get("back_to") is not None:
            problems.append(Problem(where, "only back rejection can name back_to"))
        if contract.get("mode", "single") != "single":
            problems.append(Problem(where, "competing attempts are not enabled"))
        reserve = contract.get("reserve", DEFAULT_RESERVE)
        if (
            not isinstance(reserve, str)
            or not re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,4})?", reserve)
            or Decimal(reserve) <= 0
        ):
            problems.append(Problem(where, "reserve must be a positive amount"))
        for key, minimum, maximum, default in (
            ("max_reworks", 0, 5, 1),
            ("max_backs", 0, 5, 1),
            ("input_max_chars", 500, 200000, DEFAULT_INPUT_MAX_CHARS),
        ):
            value = contract.get(key, default)
            if type(value) is not int or not minimum <= value <= maximum:
                problems.append(Problem(where, f"invalid {key}"))
        tools = contract.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
            problems.append(Problem(where, "tools must be names"))
        elif len(tools) != len(set(tools)):
            problems.append(Problem(where, "tools must be unique"))
        rules = contract.get("acceptance", [])
        if not isinstance(rules, list) or any(
            not isinstance(r, dict) or not isinstance(r.get("kind"), str) for r in rules
        ):
            problems.append(Problem(where, "invalid acceptance rules"))
            continue
        if not {"json_object", "required_keys", "no_positioning_advice"} <= {
            r["kind"] for r in rules
        }:
            problems.append(Problem(where, "mandatory output checks are missing"))
        for rule in rules:
            source = rule.get("source")
            if isinstance(source, str) and source.split(".")[0] not in refs:
                problems.append(
                    Problem(where, "acceptance must read a declared input slot")
                )
    if problems:
        return problems
    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(sid: str) -> None:
        if sid not in units or sid in visiting:
            raise ValueError("unknown or cyclic method input")
        if sid in visited:
            return
        visiting.add(sid)
        for ref in units[sid].get("input_refs", []):
            visit(ref)
        visiting.remove(sid)
        visited.add(sid)
        ordered.append(units[sid])

    try:
        for sid in sorted(required):
            visit(sid)
        if len(visited) > planning.max_steps:
            problems.append(
                Problem(
                    "pack.planning.max_steps",
                    "cannot cover required methods and inputs",
                )
            )
        for sid in units:
            visit(sid)
    except ValueError as exc:
        return [*problems, Problem("pack.method_units", str(exc))]
    # 仅为复用固定步骤的局部检查排序；不把它作为任务计划保存或交给运行方。
    fixed = {
        k: v
        for k, v in pack.items()
        if k not in {"runtime_contract", "planning", "method_units"}
    }
    return [*problems, *pack_problems({**fixed, "workflow": ordered})]


# ---------- 导入：清单拆回格子 ----------
_OUTPUT_LINE = re.compile(
    r"^- (?P<name>[^：]+)：(?P<kind>数|文字|列表|表)"
    r"(?:，(?:每项有|每行有)：(?P<items>[^；]*))?(?:；.*)?$"
)
_FIELD = re.compile(r"^(?P<name>.+)（(?P<kind>数|文字)）$")
_WORD_TYPE = {v: k for k, v in TYPE_WORDS.items()}
_FIELD_TYPE = {v: k for k, v in FIELD_WORDS.items()}


def _parse_ours(text: str) -> tuple[str, list[str], list[dict[str, Any]]] | None:
    """按第 7.3 节的模板拆回（做什么、规则、交回项）；不是这个模板拼的回 None。"""
    head = PLATFORM_HEAD + "\n\n" + STEP_LINE
    tail = "\n\n" + PLATFORM_TAIL
    if not text.startswith(head) or not text.endswith(tail):
        return None
    middle = text[len(head) : -len(tail)]
    marker = "\n\n" + OUTPUTS_LINE + "\n"
    cut = middle.rfind(marker)
    if cut < 0:
        return None
    front, lines = middle[:cut], middle[cut + len(marker) :].split("\n")
    outputs: list[dict[str, Any]] = []
    for line in lines:
        found = _OUTPUT_LINE.match(line)
        if not found:
            return None
        output: dict[str, Any] = {
            "name": found["name"],
            "type": _WORD_TYPE[found["kind"]],
        }
        if found["items"] is not None:
            fields = []
            for part in found["items"].split("、"):
                field = _FIELD.match(part)
                if not field:
                    return None
                fields.append(
                    {"name": field["name"], "type": _FIELD_TYPE[field["kind"]]}
                )
            output["items"] = fields
        outputs.append(output)
    uses_cut = front.rfind("\n\n" + USES_LINE)
    if uses_cut >= 0 and "\n" not in front[uses_cut + 2 :]:
        front = front[:uses_cut]
    rules: list[str] = []
    rules_cut = front.find("\n\n" + RULES_LINE + "\n1. ")
    if rules_cut >= 0:
        listed = front[rules_cut + len(RULES_LINE) + 3 :]
        front = front[:rules_cut]
        pieces = re.split(r"\n(?=\d+\. )", listed)
        for number, piece in enumerate(pieces, 1):
            prefix = f"{number}. "
            if not piece.startswith(prefix):
                return None
            rules.append(piece[len(prefix) :])
    return front, rules, outputs


def _guess_outputs(schema: Any) -> list[dict[str, Any]]:
    """别处来的交回结构：按键猜交回项（猜不准的，原结构照样留在「高级」里）。"""
    if not isinstance(schema, dict):
        return []
    found = []
    for key, value in schema.items():
        if not isinstance(key, str) or not _NAME.match(key):
            continue
        if value == "number" or (isinstance(value, dict) and "value" in value):
            found.append({"name": key, "type": "number"})
        elif isinstance(value, list):
            item = value[0] if value else None
            fields = [
                {"name": k, "type": "number" if v == "number" else "text"}
                for k, v in (item.items() if isinstance(item, dict) else [])
                if isinstance(k, str) and _NAME.match(k)
            ]
            found.append(
                {"name": key, "type": "list", **({"items": fields} if fields else {})}
            )
        else:
            found.append({"name": key, "type": "text"})
    return found


def _check_of(
    rule: dict[str, Any], artifacts: Mapping[str, str]
) -> dict[str, Any] | None:
    """运行方的一条验收拆回模板；拆不回的回 None。"""
    kind = rule.get("kind")
    path = rule.get("path")
    params: dict[str, Any] = {}
    if kind == "required_keys":
        return {"template": "required", "params": {"keys": list(rule.get("keys", []))}}
    if kind == "max_chars":
        return {"template": "max_chars", "params": {"max": rule.get("max_chars")}}
    if kind not in TEMPLATES or not isinstance(path, str):
        return None
    params["path"] = path
    if kind == "list_min" and rule.get("min_items", 1) != 1:
        params["min"] = rule["min_items"]
    elif kind == "each_has":
        params["keys"] = list(rule.get("keys", []))
        if rule.get("field") and rule.get("values"):
            params |= {"field": rule["field"], "values": list(rule["values"])}
    elif kind == "all_equal":
        params |= {"field": rule.get("field"), "equals": rule.get("equals")}
    elif kind == "computed" and rule.get("expression") is not None:
        params["expression"] = rule["expression"]
    elif kind == "quoted" and rule.get("field") is not None:
        params |= {"field": rule["field"], "equals": rule.get("equals")}
    elif kind == "covers":
        source = str(rule.get("source") or "")
        artifact, _, source_path = source.partition(".")
        if artifact not in artifacts or not source_path:
            return None
        params |= {
            "field": rule.get("field"),
            "source_step": artifacts[artifact],
            "source_path": source_path,
        }
        if rule.get("source_field"):
            params["source_field"] = rule["source_field"]
        if rule.get("also"):
            params["also"] = list(rule["also"])
    if kind in ("computed", "identity", "sourced") and rule.get("tolerance"):
        params["tolerance"] = rule["tolerance"]
    return {"template": kind, "params": params}


DROPPED_STEP_KEYS = (
    "tool_policy",
    "max_backs",
    "mode",
    "step_version",
)


def decompile(manifest: object) -> tuple[WorkdraftBody, list[str]]:
    """一份清单 v2 拆回工作稿的格子：（工作稿，没能留下的东西）。

    不合格抛 ValueError。
    """
    if not isinstance(manifest, dict) or manifest.get("schema") not in (
        MANIFEST_SCHEMA,
        DYNAMIC_MANIFEST_SCHEMA,
    ):
        raise ValueError("only a v2 fixed or v3 dynamic manifest is imported")
    dynamic = manifest["schema"] == DYNAMIC_MANIFEST_SCHEMA
    pack = manifest.get("pack")
    if (
        dynamic
        and isinstance(pack, dict)
        and pack.get("runtime_contract") == "knowbench/dynamic-methods/v2"
    ):
        from app.domain.knowbots.method_composition import restore

        try:
            return restore(manifest), []
        except (KeyError, TypeError, StopIteration) as exc:
            raise ValueError("invalid referenced method compilation") from exc
    workflow = pack.get("workflow") if isinstance(pack, dict) else None
    if dynamic:
        issues = pack_problems(pack)
        if issues:
            raise ValueError("invalid dynamic catalog: " + issues[0].message)
        assert isinstance(pack, dict)
        workflow = [unit["contract"] for unit in pack["method_units"]]
    elif isinstance(pack, dict) and any(
        k in pack for k in ("runtime_contract", "planning", "method_units")
    ):
        raise ValueError("dynamic methods require a v3 manifest")
    if not isinstance(pack, dict) or not isinstance(workflow, list) or not workflow:
        raise ValueError("the manifest has no pack.workflow")
    notes = [
        f"pack {key} was dropped"
        for key in ("dossier", "answers", "tool_policy")
        if pack.get(key)
    ]
    raw_scope = pack.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    artifacts: dict[str, str] = {}  # 交回物名 → 步骤编号
    all_titles = {s["step_id"]: s["title"] for s in workflow} if dynamic else {}
    if dynamic:
        artifacts = {s["output_artifact"]: s["step_id"] for s in workflow}
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(workflow):
        if not isinstance(raw, dict):
            raise ValueError(f"step {index + 1} is not an object")
        sid = raw.get("step_id")
        if not isinstance(sid, str) or not _STEP.match(sid):
            raise ValueError(f"step {index + 1} has no usable step_id")
        unknown = sorted(set(raw) - STEP_KEYS)
        if unknown:
            # 收下的清单同编出的一样只认清单 v2 的步骤字段；`missing_data`（缺数据
            # 的需求）随 knowbench 那一套删了（decisions 10-29「发版前补齐」①），
            # 不留兼容（⑰）
            raise ValueError(f"step {sid} has unknown fields: {', '.join(unknown[:5])}")
        text = str(raw.get("method_text") or "")
        uses = [artifacts[a] for a in raw.get("input_refs") or [] if a in artifacts]
        if len(uses) != len(raw.get("input_refs") or []):
            notes.append(f"{sid}: input_refs that name no earlier step were dropped")
        checks = []
        rules = [r for r in raw.get("acceptance") or [] if isinstance(r, dict)]
        for rule in rules:
            if rule.get("kind") == "json_object" or (
                rule.get("kind") == "no_positioning_advice" and not rule.get("path")
            ):
                continue
            check = _check_of(rule, artifacts)
            if check is None:
                notes.append(f"{sid}: acceptance {rule.get('kind')} was dropped")
            else:
                checks.append(check)
        ask_before = [
            {
                "id": a.get("id"),
                "question": a.get("question", ""),
                "options": [
                    o.get("label") or o.get("id") for o in a.get("options") or []
                ],
            }
            for a in raw.get("ask_before") or []
            if isinstance(a, dict)
        ]
        on_reject = raw.get("on_reject", "rework")
        on_fail: dict[str, Any] = {
            "kind": {"back": "back", "human": "ask"}.get(on_reject, "rework")
        }
        if on_reject == "back":
            on_fail["back_to"] = raw.get("back_to")
        step: dict[str, Any] = {
            "id": sid,
            "title": str(raw.get("title") or ""),
            "why": str(raw.get("why") or ""),
            "uses": uses,
            "ask_before": ask_before,
            "on_fail": on_fail,
        }
        ours = _parse_ours(text)
        if ours is not None:
            does, parsed_rules, outputs = ours
            step |= {
                "does": does,
                "rules": [
                    {"id": f"r{i}", "text": t} for i, t in enumerate(parsed_rules, 1)
                ],
                "outputs": outputs,
            }
        else:
            step |= {
                "does": text,
                "rules": [],
                "outputs": _guess_outputs(raw.get("output_schema")),
            }
        keys = {o["name"] for o in step["outputs"]}
        # 交回项自动带的「必须有」不再当作者选的
        for check in list(checks):
            if check["template"] == "required" and set(check["params"]["keys"]) == keys:
                checks.remove(check)
        step["checks"] = checks
        try:
            cell = StepCell.model_validate(step | {"advanced": {}})
        except ValidationError as exc:
            raise ValueError(f"step {sid} does not fit a workdraft: {exc}") from None
        if (
            ours is not None
            and method_text(
                cell, all_titles or {s["id"]: s["title"] for s in [*steps, step]}
            )
            != text
        ):
            # 拆得不对（比如规则里自带「2. 」换行）：整段方法原文放进「做什么」
            cell = StepCell.model_validate(
                step
                | {
                    "does": text,
                    "rules": [],
                    "outputs": _guess_outputs(raw.get("output_schema")),
                    "advanced": {},
                }
            )
        advanced: dict[str, Any] = {}
        tools = raw.get("tools")
        if isinstance(tools, list) and tuple(tools) != DEFAULT_TOOLS:
            advanced["tools"] = tools
        if raw.get("reserve") not in (None, DEFAULT_RESERVE):
            advanced["reserve"] = str(raw["reserve"])
        if raw.get("input_max_chars") not in (None, DEFAULT_INPUT_MAX_CHARS):
            advanced["input_max_chars"] = raw["input_max_chars"]
        schema = raw.get("output_schema")
        if isinstance(schema, dict) and schema != output_schema(cell):
            advanced["output_schema"] = schema
        if raw.get("output_file"):
            advanced["output_file"] = raw["output_file"]
        if raw.get("max_reworks", DEFAULT_MAX_REWORKS) != DEFAULT_MAX_REWORKS:
            notes.append(f"{sid}: max_reworks is back to {DEFAULT_MAX_REWORKS}")
        for key in DROPPED_STEP_KEYS:
            if raw.get(key) not in (None, "1", "single", 1):
                notes.append(f"{sid}: {key} was dropped")
        try:
            advanced_cell = Advanced.model_validate(advanced)
        except ValidationError as exc:
            raise ValueError(f"step {sid} engineering does not fit: {exc}") from None
        cell = cell.model_copy(update={"advanced": advanced_cell})
        dumped = cell.model_dump(mode="json")
        if dynamic:
            dumped["selection"] = pack["method_units"][index]["selection"]
        steps.append(dumped)
        artifact = raw.get("output_artifact")
        if isinstance(artifact, str):
            artifacts[artifact] = sid
    try:
        body = WorkdraftBody.model_validate(
            {
                "name": str(manifest.get("name") or "")[:80],
                "tagline": str(manifest.get("tagline") or "")[:200],
                "scope": {
                    k: str(scope.get(k) or "")
                    for k in ("does", "does_not", "elsewhere")
                },
                "steps": steps,
                **({"planning": pack["planning"]} if dynamic else {}),
            }
        )
    except ValidationError as exc:
        raise ValueError(f"the manifest does not fit a workdraft: {exc}") from None
    if dynamic and notes:
        # 不能把冻结的检查/权限丢掉后继续当作同一份作者方法。
        raise ValueError("dynamic import would lose author constraints: " + notes[0])
    return body, notes
