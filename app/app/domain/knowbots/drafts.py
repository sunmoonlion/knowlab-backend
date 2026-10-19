"""分身草稿：状态、提交内容的形状、清单在 knowlab 这边要先核的几项（SDD 0027）。

草稿状态：待测试 → 测试中 → 测试通过 → 已交付；测试中 → 测试未通过；测试中 → 待测试（调
运行方失败，或超时由管理端退回）。只有测试通过的才交付 knowmart。

版本号在建草稿时定为「这个分身已交付几次 + 1」：测的是哪份清单，交付的就是哪份（摘要要对
得上）。一次没测过、改出的新草稿沿用同一个版本号，修订号递增。

补充方法（SDD 0035）：用户对自己的想法写的改动，原话进规则出处——来源仍是 `user_text`，
`ref` 以 `amendment:<补充 id>` 开头。状态从各稿清单推：没被哪一稿引、也没「不照做」就是
待处理；建稿之前提交的补充，这一稿要么引了、要么不照做，才能核对出处。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowbots.test_run import RunCase

DraftStatus = Literal["pending_test", "testing", "passed", "failed", "delivered"]
TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("pending_test", "testing"),
        ("testing", "pending_test"),
        ("testing", "passed"),
        ("testing", "failed"),
        ("passed", "delivered"),
    }
)
MANIFEST_SCHEMA = "knowbot-manifest/v1"
RULE_SOURCES = frozenset({"user_text", "user_confirmed"})


MAX_AMENDMENT_CHARS = 4000
MAX_AMENDMENTS = 200  # 一个想法最多这么多条补充
MAX_DECLINE_CHARS = 500
AMENDMENT_PREFIX = "amendment:"
_AMENDMENT_REF = re.compile(
    r"^amendment:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f-])"
)


def can_move(current: str, target: str) -> bool:
    return (current, target) in TRANSITIONS


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SixAnswers(Strict):
    """方法说得清的六问（0027 决定 3）。"""

    solves: str = Field(min_length=1, max_length=4000)  # 解决什么
    not_solves: str = Field(min_length=1, max_length=4000)  # 不解决什么
    inputs: str = Field(min_length=1, max_length=4000)  # 输入是什么
    steps: str = Field(min_length=1, max_length=8000)  # 步骤与每步的判断规则
    outputs: str = Field(min_length=1, max_length=4000)  # 输出长什么样
    correctness: str = Field(min_length=1, max_length=4000)  # 怎样算对


class Idea(Strict):
    """用户的方法：六问，或一份 skill 原文。逻辑只来自这里（C-L1）。"""

    kind: Literal["six_questions", "skill"]
    answers: SixAnswers | None = None
    skill: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def _one_form(self) -> Idea:
        if self.kind == "six_questions" and (self.answers is None or self.skill):
            raise ValueError("six_questions needs answers and no skill")
        if self.kind == "skill" and (not self.skill or self.answers is not None):
            raise ValueError("skill needs the skill text and no answers")
        return self


# 用户同意的是哪一版测试数据说明（web端提交表单那一句）。
# 1：「测试产出不给任何人看，7 天后删除」。
# 2（SDD 0037）：成稿与各步结果只给本人和做测试的工作人员看，交付或被下一个分身
# 取代 90 天后清掉。老创意是在 1 下同意的：不取、不显示产出，用户点一次「同意新
# 说明」才升到 2（审读 10-19）。
CONSENT_VERSION = 2
OUTPUTS_KEEP_DAYS = 90
OUTPUTS_EXPIRED = (
    "已按期清除：这个分身交付或被下一个分身取代 90 天后，成稿与交回物不再保留"
)


class KnowbotRequestIn(Strict):
    title: str = Field(min_length=1, max_length=80)
    idea: Idea
    runtime: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    cases: tuple[RunCase, ...] = Field(min_length=1, max_length=50)
    # 用户同意他上传的数据用于测试（0027 决定 11 ④），不同意不受理
    consent_test_data: Literal[True]

    @model_validator(mode="after")
    def _unique_cases(self) -> KnowbotRequestIn:
        ids = [c.case_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


# 与 knowmart 交付时的上限一致（审读第 10 条）：草稿测过了、交付才被拒，白测一轮
MAX_MANIFEST_BYTES = 1_000_000
MAX_RULE_SOURCES = 500
MAX_DATA_BINDINGS = 64


def _size(document: dict[str, Any]) -> int:
    """按 knowmart 算摘要的写法量大小（紧凑、键排序、不转义中文）。"""
    return len(
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def draft_manifest_problems(
    manifest: object, *, runtime: str, version: str
) -> list[str]:
    """草稿清单在 knowlab 这边先核的几项。

    完整外层由 knowmart 交付时核，pack 由运行方核。
    """
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    doc: dict[str, Any] = manifest
    if _size(doc) > MAX_MANIFEST_BYTES:
        return [f"manifest is larger than {MAX_MANIFEST_BYTES} bytes"]
    problems: list[str] = []
    if doc.get("schema") != MANIFEST_SCHEMA:
        problems.append(f"schema must be {MANIFEST_SCHEMA}")
    if doc.get("runtime") != runtime:
        problems.append(f"runtime must be {runtime}")
    if doc.get("version") != version:
        problems.append(f"version must be {version}")
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name is required")
    pack = doc.get("pack")
    if not isinstance(pack, dict) or not pack.get("pack_id"):
        problems.append("pack.pack_id is required")
    elif not isinstance(pack.get("workflow"), list) or not pack["workflow"]:
        problems.append("pack.workflow must be a non-empty list")
    elif str(pack.get("version")) != version:
        problems.append("pack.version must equal the version")
    sources = doc.get("rule_sources")
    if not isinstance(sources, list) or not sources:
        problems.append("every rule needs a source (rule_sources is empty)")
    elif len(sources) > MAX_RULE_SOURCES:
        problems.append(f"at most {MAX_RULE_SOURCES} rule sources")
    elif any(
        not isinstance(s, dict)
        or s.get("source") not in RULE_SOURCES
        or not s.get("ref")
        for s in sources
    ):
        problems.append(
            "each rule source must be user_text or user_confirmed with a ref"
        )
    bindings = doc.get("data_bindings", [])
    if not isinstance(bindings, list):
        problems.append("data_bindings must be a list")
    elif len(bindings) > MAX_DATA_BINDINGS:
        problems.append(f"at most {MAX_DATA_BINDINGS} data bindings")
    else:
        ids = [b.get("dataset_id") for b in bindings if isinstance(b, dict)]
        if len(ids) != len(set(ids)):
            problems.append("a dataset may be bound only once per version")
    return problems


def amendment_refs(manifest: object) -> tuple[set[str], list[str]]:
    """清单的规则出处引了哪几条补充（id），以及写坏了的 `amendment:` 引用。"""
    found: set[str] = set()
    broken: list[str] = []
    sources = manifest.get("rule_sources") if isinstance(manifest, dict) else None
    for source in sources if isinstance(sources, list) else []:
        ref = source.get("ref") if isinstance(source, dict) else None
        if not isinstance(ref, str) or not ref.startswith(AMENDMENT_PREFIX):
            continue
        match = _AMENDMENT_REF.match(ref)
        if match and source.get("source") == "user_text":
            found.add(match.group(1))
        else:
            broken.append(ref[:80])
    return found, broken


def amendment_ref_problems(manifest: object, amendments: dict[str, bool]) -> list[str]:
    """建草稿时核：引的补充必须是这个想法的、没被「不照做」。

    amendments：这个想法的补充 id → 是否已不照做。
    """
    cited, broken = amendment_refs(manifest)
    problems = [
        f"rule source {ref!r} must be user_text with amendment:<id>" for ref in broken
    ]
    for amendment_id in sorted(cited):
        if amendment_id not in amendments:
            problems.append(f"amendment {amendment_id} is not one of this request's")
        elif amendments[amendment_id]:
            problems.append(f"amendment {amendment_id} was declined")
    return problems


def amendment_status(
    amendment_id: str, declined: bool, drafts: Iterable[tuple[int, object]]
) -> tuple[str, int | None]:
    """（open / used / declined，用进的最新一稿的修订号）。drafts：（修订号，清单）。"""
    if declined:
        return "declined", None
    used = [
        rev for rev, manifest in drafts if amendment_id in amendment_refs(manifest)[0]
    ]
    return ("used", max(used)) if used else ("open", None)


def amendments_to_handle(
    manifest: object,
    draft_created_at: datetime | None,
    amendments: Iterable[tuple[str, datetime | None, bool]],
) -> tuple[list[str], int]:
    """（这一稿核对出处前要处理的补充 id，这一稿建好之后才提交的条数）。

    amendments：（id，提交时间，是否已不照做）。建稿之前（含同一刻）提交的、没不照做的，
    这一稿的出处必须引它；上一稿引过不算（改版不许悄悄丢掉用户的补充，SDD 0035 §二.5）。
    """
    cited = amendment_refs(manifest)[0]
    blocking: list[str] = []
    newer = 0
    for amendment_id, created_at, declined in amendments:
        before = (
            created_at is None
            or draft_created_at is None
            or created_at <= draft_created_at
        )
        if not before:
            newer += 0 if declined else 1
        elif not declined and amendment_id not in cited:
            blocking.append(amendment_id)
    return blocking, newer
