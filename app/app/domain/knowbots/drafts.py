"""分身草稿：状态、提交内容的形状、清单在 knowlab 这边要先核的几项（SDD 0027）。

草稿状态：待测试 → 测试中 → 测试通过 → 已交付；测试中 → 测试未通过；测试中 → 待测试（调
运行方失败，或超时由管理端退回）。只有测试通过的才交付 knowmart。

版本号在建草稿时定为「这个分身已交付几次 + 1」：测的是哪份清单，交付的就是哪份（摘要要对
得上）。一次没测过、改出的新草稿沿用同一个版本号，修订号递增。
"""

from __future__ import annotations

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


def draft_manifest_problems(
    manifest: object, *, runtime: str, version: str
) -> list[str]:
    """草稿清单在 knowlab 这边先核的几项。

    完整外层由 knowmart 交付时核，pack 由运行方核。
    """
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    doc: dict[str, Any] = manifest
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
    elif any(
        not isinstance(s, dict)
        or s.get("source") not in RULE_SOURCES
        or not s.get("ref")
        for s in sources
    ):
        problems.append(
            "each rule source must be user_text or user_confirmed with a ref"
        )
    return problems
