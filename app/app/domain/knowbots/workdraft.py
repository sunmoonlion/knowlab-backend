"""工作稿（专家孵化方案第五、七、九节，decisions ⑮）：一个名称一份，作者只写方法层。

方法层是一格一格的：整个专家一处（名称、一句话介绍、接什么 / 不接什么 / 不接的去哪问、
挑的数据），每一步一处（叫什么、做什么、为什么、用上哪几步、规则、开步前问什么、交回哪几项、
怎样算对、没过怎么办）。「高级」里是工程层（工具、额度、读入上限、手写结构、成稿文件），
懂行的人才改；工程层、步骤顺序、用上哪几步、没过怎么办、挑的数据都不是作者的判断文字，不
要出处。清单由 compile 按格子生成。

格子的路径：`name`、`tagline`、`scope/does`、`scope/does_not`、`scope/elsewhere`、
`steps/<步>/title`、`steps/<步>/does`、`steps/<步>/why`、`steps/<步>/rules/<规则>`、
`steps/<步>/ask_before/<问题>`、`steps/<步>/outputs/<交回项>`、`steps/<步>/checks/<第几条>`。
后四种是结构化的格子，格子文字是它的规范 JSON（键排序、紧凑、不转义中文）。

出处（第 9.1 节）：每个有字的格子带一条 {kind, ref}：

- 作者在编辑器里存的：`user_text`，`record:<编辑记录>`；
- 导入专家包、复制自己的专家：`user_text`，`record:<那条导入 / 复制记录>`；
- 接受了的建议（以后）：`user_confirmed`，`record:<接受记录>`；
- 答过的缺口：`user_confirmed`，`question:<问题>`（作者本人答过的那一条）。

原话核对：引的记录要在、属于这份工作稿、是作者本人写的，记录里的文字与格子逐字一致
（导入、复制记录里记着它拆出的每一格的文字）。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

STEP_ID = r"^[A-Za-z][A-Za-z0-9_-]{0,47}$"
CELL_ID = r"^[A-Za-z0-9_-]{1,32}$"
# 交回项与它每项的名字是交回 JSON 的键：不带点（验收按点分路径）、斜杠（格子路径）、
# 空白、引号，也不带拼说明用的几个中文标点
NAME = r"^[^\s./\\\"'（）()、，,；;：:]{1,64}$"
MAX_STEPS = 30
MAX_BODY_BYTES = 500_000
MAX_EDITS = 200

RECORD_KINDS = ("edit", "accept", "import", "copy")
ACTOR_ROLES = ("author", "operator")
RECORD_REF = re.compile(
    r"^record:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
QUESTION_REF = re.compile(
    r"^question:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------- 方法层 ----------
class ScopeCell(Strict):
    does: str = Field(default="", max_length=2000)  # 接什么
    does_not: str = Field(default="", max_length=2000)  # 不接什么
    elsewhere: str = Field(default="", max_length=2000)  # 不接的去哪问


class RuleCell(Strict):
    id: str = Field(pattern=CELL_ID)
    text: str = Field(default="", max_length=4000)


class AskBeforeCell(Strict):
    """开步前问用户什么：问题，可以给几个选项（选项是给用户看的字）。"""

    id: str = Field(pattern=CELL_ID)
    question: str = Field(default="", max_length=1000)
    options: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def _short_options(self) -> AskBeforeCell:
        if any(not o.strip() or len(o) > 200 for o in self.options):
            raise ValueError("each option is 1 to 200 characters")
        return self


class OutputField(Strict):
    """列表、表的每项有哪几项。"""

    name: str = Field(pattern=NAME)
    type: Literal["number", "text"] = "text"


class OutputCell(Strict):
    """交回哪几项：名字、类型（数、表、文字、列表）；列表与表再列每项有哪几项。"""

    name: str = Field(pattern=NAME)
    type: Literal["number", "table", "text", "list"]
    items: tuple[OutputField, ...] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def _items_for_rows(self) -> OutputCell:
        if self.items is not None and self.type not in ("list", "table"):
            raise ValueError("only a list or a table has items")
        names = [f.name for f in self.items or ()]
        if len(names) != len(set(names)):
            raise ValueError("item names must be unique")
        return self


CheckTemplate = Literal[
    "required",  # 必须有
    "non_empty",  # 不能空
    "list_min",  # 列表至少几项
    "list_empty",  # 列表必须为空
    "blank",  # 这一项留空
    "each_has",  # 每项都有哪几项 / 某项只能取哪几个值
    "all_equal",  # 每项的某项都等于
    "computed",  # 数是用计算工具算出来的（可以指定算式）
    "identity",  # 两个数相等（恒等式、加总），容差多少
    "sourced",  # 数和原数据那一格对得上
    "quoted",  # 说法要附原文引文
    "covers",  # 上一步列出的每一项这一步都要有，不多不少
    "max_chars",  # 字数上限
]


class CheckCell(Strict):
    """怎样算对：选一个验收模板，再填参数（参数在 compile 时逐个核）。"""

    template: CheckTemplate
    params: dict[str, Any] = Field(default_factory=dict)


class OnFail(Strict):
    """没过怎么办：默认重做一次，再不过停下来问；可以改成退回前面某一步，或直接问。"""

    kind: Literal["rework", "back", "ask"] = "rework"
    back_to: str | None = Field(default=None, pattern=STEP_ID)


class Advanced(Strict):
    """工程层（第 7.2 节「高级」）：不填就用程序生成的。"""

    tools: tuple[str, ...] | None = Field(default=None, max_length=30)
    reserve: str | None = Field(default=None, pattern=r"^[0-9]{1,4}(\.[0-9]{1,4})?$")
    input_max_chars: int | None = Field(default=None, ge=500, le=200_000)
    output_schema: dict[str, Any] | None = None
    output_file: str | None = Field(default=None, max_length=256)


class StepCell(Strict):
    id: str = Field(pattern=STEP_ID)
    title: str = Field(default="", max_length=80)
    does: str = Field(default="", max_length=20_000)
    why: str = Field(default="", max_length=1000)
    uses: tuple[str, ...] = Field(default=(), max_length=MAX_STEPS)
    rules: tuple[RuleCell, ...] = Field(default=(), max_length=50)
    ask_before: tuple[AskBeforeCell, ...] = Field(default=(), max_length=10)
    outputs: tuple[OutputCell, ...] = Field(default=(), max_length=30)
    checks: tuple[CheckCell, ...] = Field(default=(), max_length=50)
    on_fail: OnFail = OnFail()
    advanced: Advanced = Advanced()

    @model_validator(mode="after")
    def _unique(self) -> StepCell:
        for kind, ids in (
            ("rule", [r.id for r in self.rules]),
            ("question", [a.id for a in self.ask_before]),
            ("output", [o.name for o in self.outputs]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{kind} ids must be unique in a step")
        return self


class WorkdraftBody(Strict):
    name: str = Field(default="", max_length=80)
    tagline: str = Field(default="", max_length=200)
    scope: ScopeCell = ScopeCell()
    # 从作者自己的 knowbase 挑的资料编号：交付时才冻结成 bindings，草稿里不写
    data_picks: tuple[str, ...] = Field(default=(), max_length=200)
    steps: tuple[StepCell, ...] = Field(default=(), max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _consistent(self) -> WorkdraftBody:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        if any(not p or len(p) > 64 for p in self.data_picks):
            raise ValueError("each data pick is 1 to 64 characters")
        return self


def empty_body() -> dict[str, Any]:
    return WorkdraftBody().model_dump(mode="json")


def canonical(value: Any) -> str:
    """结构化格子的文字：规范 JSON（键排序、紧凑、不转义中文）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def size_of(body: Mapping[str, Any]) -> int:
    return len(canonical(body).encode("utf-8"))


def cells(body: WorkdraftBody) -> dict[str, str]:
    """方法层有字的格子：路径 → 格子文字。这些都要有出处；空的不算。"""
    found: dict[str, str] = {}

    def text(path: str, value: str) -> None:
        if value.strip():
            found[path] = value

    text("name", body.name)
    text("tagline", body.tagline)
    text("scope/does", body.scope.does)
    text("scope/does_not", body.scope.does_not)
    text("scope/elsewhere", body.scope.elsewhere)
    for step in body.steps:
        base = f"steps/{step.id}"
        text(f"{base}/title", step.title)
        text(f"{base}/does", step.does)
        text(f"{base}/why", step.why)
        for rule in step.rules:
            text(f"{base}/rules/{rule.id}", rule.text)
        for ask in step.ask_before:
            found[f"{base}/ask_before/{ask.id}"] = canonical(
                ask.model_dump(mode="json")
            )
        for output in step.outputs:
            found[f"{base}/outputs/{output.name}"] = canonical(
                output.model_dump(mode="json", exclude_none=True)
            )
        for index, check in enumerate(step.checks):
            found[f"{base}/checks/{index}"] = canonical(check.model_dump(mode="json"))
    return found


# ---------- 改格子 ----------
STEP_FIELDS = frozenset(
    {
        "title",
        "does",
        "why",
        "uses",
        "rules",
        "ask_before",
        "outputs",
        "checks",
        "on_fail",
        "advanced",
    }
)
# 一条一条的格子：怎么认出是哪一条
KEYED = {"rules": "id", "ask_before": "id", "outputs": "name"}


def _keyed(items: list[Any], key: str, name: str, value: Any) -> list[Any]:
    """按编号（名字）改一条：value 为 None 删掉，没有的加在最后。"""
    kept = [i for i in items if not (isinstance(i, dict) and i.get(key) == name)]
    if value is None:
        if len(kept) == len(items):
            raise ValueError(f"no {name} to remove")
        return kept
    if not isinstance(value, dict):
        raise ValueError("the cell takes an object")
    entry = {**value, key: name}
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get(key) == name:
            return [*items[:index], entry, *items[index + 1 :]]
    return [*items, entry]


def apply(body: Mapping[str, Any], path: str, value: Any) -> dict[str, Any]:
    """改一处，回改后的整份（还没核形状，核在 WorkdraftBody）。

    path 不认得抛 ValueError。

    可以改一格，也可以整块换（`scope`、`steps`、`steps/<步>`、`steps/<步>/rules` ……）；
    `steps/<步>`、`steps/<步>/rules/<规则>` 这类给 None 是删掉，没有的加在最后；
    `steps/<步>/checks/<n>` 的 n 等于现有条数是加一条。
    """
    doc = json.loads(json.dumps(dict(body)))
    parts = path.split("/") if path else []
    if not parts or any(not p for p in parts):
        raise ValueError("empty path")
    head = parts[0]
    if head in ("name", "tagline", "data_picks", "steps", "scope") and len(parts) == 1:
        doc[head] = value
        return doc
    if head == "scope" and len(parts) == 2:
        if parts[1] not in ("does", "does_not", "elsewhere"):
            raise ValueError(f"unknown cell {path}")
        doc["scope"] = {**(doc.get("scope") or {}), parts[1]: value}
        return doc
    if head != "steps":
        raise ValueError(f"unknown cell {path}")
    steps: list[Any] = list(doc.get("steps") or [])
    sid = parts[1]
    if len(parts) == 2:
        doc["steps"] = _keyed(steps, "id", sid, value)
        return doc
    index = next(
        (i for i, s in enumerate(steps) if isinstance(s, dict) and s.get("id") == sid),
        None,
    )
    if index is None:
        raise ValueError(f"no step {sid}")
    step = dict(steps[index])
    field = parts[2]
    if field not in STEP_FIELDS:
        raise ValueError(f"unknown cell {path}")
    if len(parts) == 3:
        step[field] = value
    elif len(parts) == 4 and field in KEYED:
        if field == "rules" and isinstance(value, str):
            value = {"text": value}  # 规则的文字可以直接给
        step[field] = _keyed(list(step.get(field) or []), KEYED[field], parts[3], value)
    elif len(parts) == 4 and field == "checks":
        checks = list(step.get("checks") or [])
        if not parts[3].isdigit() or int(parts[3]) > len(checks):
            raise ValueError(f"no check {parts[3]}")
        n = int(parts[3])
        if value is None:
            if n == len(checks):
                raise ValueError(f"no check {n}")
            del checks[n]
        elif n == len(checks):
            checks.append(value)
        else:
            checks[n] = value
        step["checks"] = checks
    else:
        raise ValueError(f"unknown cell {path}")
    steps[index] = step
    doc["steps"] = steps
    return doc


# ---------- 出处 ----------
class Entry(Protocol):
    """一条编辑 / 接受 / 导入 / 复制记录（knowbot_workdraft_record）：核出处要看的。"""

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def workdraft_id(self) -> uuid.UUID: ...
    @property
    def kind(self) -> str: ...
    @property
    def actor_subject(self) -> str: ...
    @property
    def actor_role(self) -> str: ...
    @property
    def path(self) -> str | None: ...
    @property
    def text(self) -> str: ...


@dataclass(frozen=True)
class Problem:
    """哪一格（或清单的哪一处）不行、为什么。页面按 path 标红那一格。"""

    path: str
    message: str


def bundle_text(origin: str, found: Mapping[str, str], **extra: str) -> str:
    """导入、复制记录的文字：从哪来，拆出的每一格的文字（原话核对逐格比）。"""
    return canonical({"from": origin, **extra, "cells": dict(found)})


def _bundled(entry: Entry, path: str) -> str | None:
    try:
        found = json.loads(entry.text).get("cells")
    except (ValueError, AttributeError):
        return None
    value = found.get(path) if isinstance(found, dict) else None
    return value if isinstance(value, str) else None


def record_ids(sources: Mapping[str, Any]) -> set[uuid.UUID]:
    """出处里引的记录编号。"""
    found: set[uuid.UUID] = set()
    for source in sources.values():
        ref = source.get("ref") if isinstance(source, dict) else None
        match = RECORD_REF.match(ref) if isinstance(ref, str) else None
        if match:
            found.add(uuid.UUID(match.group(1)))
    return found


def origins(
    sources: Mapping[str, Any], entries: Mapping[uuid.UUID, Entry]
) -> dict[str, str]:
    """每格的出处是怎么来的（页面在格子旁小字标出）：引的记录是哪种（edit、import、copy、
    accept），或 `question`（答过的缺口）；引不上的不列（页面当没有出处）。"""
    found: dict[str, str] = {}
    for path, source in sources.items():
        ref = source.get("ref") if isinstance(source, dict) else None
        if not isinstance(ref, str):
            continue
        if QUESTION_REF.match(ref):
            found[path] = "question"
            continue
        record = RECORD_REF.match(ref)
        entry = entries.get(uuid.UUID(record.group(1))) if record else None
        if entry is not None:
            found[path] = entry.kind
    return found


def source_problems(
    body: WorkdraftBody,
    sources: Mapping[str, Any],
    *,
    workdraft_id: uuid.UUID | None,
    owner_subject: str,
    entries: Mapping[uuid.UUID, Entry],
    answered: Collection[str] = (),
) -> list[Problem]:
    """方法层每一格都要有出处，而且核得上（第 9.2 节）。

    entries：出处引到的记录（按编号；别的工作稿的也在里面，好认出来）；answered：作者本人
    答过的问题编号。
    """
    problems: list[Problem] = []
    for path, text in cells(body).items():
        source = sources.get(path)
        kind = source.get("kind") if isinstance(source, dict) else None
        ref = source.get("ref") if isinstance(source, dict) else None
        if kind not in ("user_text", "user_confirmed") or not isinstance(ref, str):
            problems.append(Problem(path, "this cell has no source"))
            continue
        question = QUESTION_REF.match(ref)
        if question:
            if kind != "user_confirmed":
                problems.append(Problem(path, "an answered question is user_confirmed"))
            elif question.group(1) not in answered:
                problems.append(
                    Problem(path, "the source is not a question the author answered")
                )
            continue
        record = RECORD_REF.match(ref)
        entry = entries.get(uuid.UUID(record.group(1))) if record else None
        if record is None:
            problems.append(
                Problem(path, "the source is neither a record nor a question")
            )
        elif entry is None:
            problems.append(Problem(path, "the source record does not exist"))
        elif entry.workdraft_id != workdraft_id:
            problems.append(Problem(path, "the source record is another workdraft's"))
        elif entry.actor_subject != owner_subject or entry.actor_role != "author":
            problems.append(Problem(path, "the source record is not the author's"))
        elif (kind == "user_confirmed") != (entry.kind == "accept"):
            problems.append(
                Problem(path, f"a {entry.kind} record cannot be cited as {kind}")
            )
        elif entry.kind in ("edit", "accept") and (
            entry.path != path or entry.text != text
        ):
            problems.append(Problem(path, "the cell differs from its record"))
        elif entry.kind in ("import", "copy") and _bundled(entry, path) != text:
            problems.append(Problem(path, "the cell differs from its record"))
    return problems
