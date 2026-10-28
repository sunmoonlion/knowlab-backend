"""knowlab 这边的孵化一轮（专家孵化方案第八、九节；第十五节第 4 步）。

- 种子：创意那一版拼成交给运行方的原文（`seed_of`）。六问逐题拼成「问：…\\n答：…」，
  题与题之间空一行；skill、一句话问题照原文。出处 idea 的 quote 要逐字出现在这份原文里，
  出一版时按同一个拼法从那一版创意重拼再核（workdraft.IDEA_REF）。
- 核交回（`review`，第九节）：每条建议引到的出处里，idea 的引文要逐字出现在发出时的
  种子里，answer 的 ref 要是这一轮带去的、作者答过的缺口。对不上的那条建议标「出处核
  不上」，不能原样接受，只能改了再接受（改过的算作者原话）。
- 接受（`apply_suggestion`、`source_of`，第 8.3 节）：照建议的 op 改工作稿，改动到的
  每一格按引到的出处记来源（idea → user_text、answer → user_confirmed、提议 → 接受
  记录）。
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from app.domain.knowbots.incubation import Handback, SourceEntry
from app.domain.knowbots.workdraft import KEYED, apply

# 六问的题目（web端写创意页的六问，次序同 SixAnswers）
SIX_QUESTIONS = (
    ("solves", "解决什么"),
    ("not_solves", "不解决什么"),
    ("inputs", "输入是什么"),
    ("steps", "步骤与每步的判断规则"),
    ("outputs", "输出长什么样"),
    ("correctness", "怎样算对"),
)
# 还没结束的一轮（契约的状态里不是终态的那几种）：同一份工作稿同时只许一轮
ACTIVE_ROUNDS = frozenset({"queued", "running", "waiting"})
# 每条建议的处理：没处理、原样接受、改了再接受、不要
DECISIONS = ("accepted", "accepted_edited", "rejected")
# 核不上的原因
QUOTE_NOT_IN_IDEA = "quote_not_in_idea"
ANSWER_NOT_FOUND = "answer_not_found"


def seed_of(idea: Mapping[str, Any]) -> tuple[str, str]:
    """创意的一版 →（种类，交给运行方的原文）。"""
    kind = idea.get("kind")
    if kind == "question":
        return "question", str(idea.get("question") or "")
    if kind == "skill":
        return "skill", str(idea.get("skill") or "")
    answers = idea.get("answers") or {}
    text = "\n\n".join(
        f"问：{title}\n答：{answers.get(key) or ''}" for key, title in SIX_QUESTIONS
    )
    return "six_questions", text


# ---------- 核交回 ----------
def related(entries: Iterable[SourceEntry], path: str) -> list[SourceEntry]:
    """出处表里和这条建议有关的行：同一格、它下面的格子，或它所在的那一块。"""
    return [
        e
        for e in entries
        if e.path == path
        or e.path.startswith(path + "/")
        or path.startswith(e.path + "/")
    ]


def review(
    handback: Handback, *, seed_text: str, answer_ids: Collection[str]
) -> dict[str, dict[str, Any]]:
    """每条建议：出处核得上没有（verified）、引的是哪几种（idea / answer / proposal；
    一条都没引的算提议）、核不上的是哪几行。"""
    found: dict[str, dict[str, Any]] = {}
    for suggestion in handback.suggestions:
        entries = related(handback.sources, suggestion.path)
        problems: list[dict[str, str]] = []
        for entry in entries:
            if entry.kind == "idea" and (entry.quote or "") not in seed_text:
                problems.append({"path": entry.path, "problem": QUOTE_NOT_IN_IDEA})
            if entry.kind == "answer" and entry.ref not in answer_ids:
                problems.append({"path": entry.path, "problem": ANSWER_NOT_FOUND})
        found[suggestion.id] = {
            "verified": not problems,
            "kinds": sorted({e.kind for e in entries}) or ["proposal"],
            "problems": problems,
        }
    return found


def source_of(entries: Iterable[SourceEntry], cell: str) -> SourceEntry | None:
    """改动到的一格出自哪一行：先找正是这一格的，再找它所在的最近一块；都没有回 None
    （当提议）。"""
    rows = list(entries)
    exact = next((e for e in rows if e.path == cell), None)
    if exact is not None:
        return exact
    above = [e for e in rows if cell.startswith(e.path + "/")]
    return max(above, key=lambda e: len(e.path), default=None)


# ---------- 照建议改工作稿 ----------
_TEXT_CELLS = ("title", "does", "why")
_LISTS = ("uses", "rules", "ask_before", "outputs", "checks")


def _steps(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(s) for s in doc.get("steps") or [] if isinstance(s, dict)]


def _step(doc: Mapping[str, Any], sid: str) -> dict[str, Any]:
    found = next((s for s in _steps(doc) if s.get("id") == sid), None)
    if found is None:
        raise ValueError(f"no step {sid}")
    return found


def _fresh(prefix: str, taken: Iterable[Any]) -> str:
    used = {str(t) for t in taken}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _moved(items: list[Any], index: int, to: int) -> list[Any]:
    item = items.pop(index)
    items.insert(min(to, len(items)), item)
    return items


def _add(doc: dict[str, Any], parts: list[str], value: Any) -> dict[str, Any]:
    if len(parts) == 2:  # 加一步
        if any(s.get("id") == parts[1] for s in _steps(doc)):
            raise ValueError(f"step {parts[1]} is already there")
        return apply(doc, "/".join(parts), value)
    step = _step(doc, parts[1])
    field = parts[2]
    if field == "checks":  # 加一条验收：插到第 n 条（不给、超出就在最后）
        checks = list(step.get("checks") or [])
        at = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else len(checks)
        checks.insert(min(at, len(checks)), value)
        return apply(doc, f"steps/{parts[1]}/checks", checks)
    if field in KEYED:
        key = KEYED[field]
        items = [i for i in step.get(field) or [] if isinstance(i, dict)]
        if len(parts) == 4:
            name = parts[3]
        elif field == "rules" and isinstance(value, str):
            name = _fresh("r", (i.get(key) for i in items))
        elif isinstance(value, dict) and isinstance(value.get(key), str):
            name = value[key]
        else:
            name = _fresh("r" if field == "rules" else "q", (i.get(key) for i in items))
        if any(i.get(key) == name for i in items):
            raise ValueError(f"{name} is already there")
        return apply(doc, f"steps/{parts[1]}/{field}/{name}", value)
    return apply(doc, "/".join(parts), value)  # 一格字：加就是写上


def _remove(doc: dict[str, Any], parts: list[str]) -> dict[str, Any]:
    path = "/".join(parts)
    if path in ("name", "tagline") or (parts[0] == "scope" and len(parts) == 2):
        return apply(doc, path, "")
    if parts == ["scope"]:
        return apply(doc, path, {})
    if len(parts) == 3 and parts[2] in _TEXT_CELLS:
        return apply(doc, path, "")
    if len(parts) == 3 and parts[2] in _LISTS:
        return apply(doc, path, [])
    if len(parts) == 3 and parts[2] == "on_fail":
        return apply(doc, path, {"kind": "rework"})
    return apply(doc, path, None)  # 一步、一条、一项、第 n 条验收


def _move(doc: dict[str, Any], parts: list[str], to: int) -> dict[str, Any]:
    if len(parts) == 2:
        steps = _steps(doc)
        index = next((i for i, s in enumerate(steps) if s.get("id") == parts[1]), None)
        if index is None:
            raise ValueError(f"no step {parts[1]}")
        return apply(doc, "steps", _moved(steps, index, to))
    if len(parts) != 4:
        raise ValueError("only a step or an item moves")
    step = _step(doc, parts[1])
    field = parts[2]
    items = list(step.get(field) or [])
    if field == "checks":
        index = int(parts[3]) if parts[3].isdigit() else -1
    elif field in KEYED:
        index = next(
            (
                i
                for i, item in enumerate(items)
                if isinstance(item, dict) and item.get(KEYED[field]) == parts[3]
            ),
            -1,
        )
    else:
        raise ValueError("only a step or an item moves")
    if not 0 <= index < len(items):
        raise ValueError(f"no {parts[3]} to move")
    return apply(doc, f"steps/{parts[1]}/{field}", _moved(items, index, to))


def apply_suggestion(
    body: Mapping[str, Any], path: str, op: str, value: Any
) -> dict[str, Any]:
    """照一条建议改工作稿，回改后的整份（形状还没核，核在 WorkdraftBody）。

    路径与 value 同工作稿格子（workdraft.apply）。add 在一组上（`steps/<步>/rules`
    这类）没给编号的发一个新编号；move 的 value 是挪到第几位（从 0 起）。改不了抛
    ValueError。
    """
    doc = json.loads(json.dumps(dict(body)))
    parts = path.split("/")
    if op == "set":
        return apply(doc, path, value)
    if op == "add":
        return _add(doc, parts, value)
    if op == "remove":
        return _remove(doc, parts)
    if op == "move":
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("move takes the new position")
        return _move(doc, parts, value)
    raise ValueError(f"unknown op {op}")
