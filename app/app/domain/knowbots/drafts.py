"""准专家（草稿）：状态、申请内容的形状、清单在 knowlab 这边要先核的几项。

SDD 0027、0037。

准专家状态：待测 → 测试中 → 通过 / 没通过；测试中 → 待测（这次测试平台出错：运行方报错、
超时、开跑失败、运营判定卡住）。测试的经过记在测试表，通过即出专家（专家表）；「已交付」
0012 起不再用。

版本号「x.y」：x 在建稿时定为「已有专家的最大版本号 + 1」，测的是哪份清单，上架的就是
哪份（摘要要对得上）；y 是修订号，在一个名称里递增。

补充方法（SDD 0035）已取消（SDD 0037 第三节）：旧稿的规则出处里 `ref` 以
`amendment:<补充 id>` 开头的照旧可查，新稿不许再引。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowbots.test_run import RunCase

DraftStatus = Literal["pending_test", "testing", "passed", "failed"]
TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("pending_test", "testing"),
        ("testing", "pending_test"),
        ("testing", "passed"),
        ("testing", "failed"),
    }
)
# 只收清单 v2（SDD 0043；decisions ⑰ 起不留 v1）：作者绑的数据交付时才冻结进
# `bindings`，草稿里不写
MANIFEST_SCHEMA = "knowbot-manifest/v2"
DYNAMIC_MANIFEST_SCHEMA = "knowbot-manifest/v3"
RULE_SOURCES = frozenset({"user_text", "user_confirmed"})


AMENDMENT_PREFIX = "amendment:"
_AMENDMENT_REF = re.compile(
    r"^amendment:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f-])"
)


def case_question_digest(case: dict[str, Any]) -> str:
    """一条用例问的是什么（问题原文的摘要）。

    答过的开步前提问只套回问的还是同一句的那条用例：改创意后编号换了位置、或问题改了字，
    旧答案不能当成用户确认过（Fable 评审 P1-6，C-L1）。
    """
    text = str((case.get("input") or {}).get("question") or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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


class IdeaMethodChoice(Strict):
    """An author's shortlist, not an executable expert binding."""

    method_id: UUID
    version: int = Field(ge=1, le=999999, strict=True)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class Idea(Strict):
    """用户的方法：六问、一份 skill 原文，或一句话问题（专家孵化方案第四节：方法由 AI
    按问题起草，作者改定）。逻辑只来自这里与作者在工作稿里写的（C-L1）。"""

    kind: Literal["six_questions", "skill", "question"]
    answers: SixAnswers | None = None
    skill: str | None = Field(default=None, max_length=20000)
    question: str | None = Field(default=None, max_length=2000)
    method_choices: tuple[IdeaMethodChoice, ...] | None = Field(
        default=None, max_length=30
    )

    template_sources: tuple[IdeaMethodChoice, ...] | None = Field(
        default=None, max_length=30
    )

    @model_validator(mode="after")
    def _one_form(self) -> Idea:
        sources = [(c.method_id, c.version) for c in (self.template_sources or ())]
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate template source version")
        ids = [choice.method_id for choice in (self.method_choices or ())]
        if len(ids) != len(set(ids)):
            raise ValueError("choose at most one version of each method")
        given = {
            "six_questions": self.answers is not None,
            "skill": bool(self.skill),
            "question": bool(self.question and self.question.strip()),
        }
        if not given[self.kind] or any(v for k, v in given.items() if k != self.kind):
            raise ValueError(f"{self.kind} needs its own text and nothing else")
        return self


# 用户同意的是哪一版测试数据说明（web端提交表单那一句）。
# 1：「测试产出不给任何人看，7 天后删除」。
# 2（SDD 0037）：成稿与各步结果只给本人和做测试的工作人员看，上架或被下一位准专家
# 取代 90 天后清掉。老创意是在 1 下同意的：不取、不显示产出，用户点一次「同意新
# 说明」才升到 2（审读 10-19）。
# 3（10-29，数据第四版与 decisions ⑯）：测试用的是本人在 knowbase 里为测试选定的资料，
# 在本人的电脑上、用本人的模型 key 跑；结果给本人看，请平台测试或交平台审核时也给做
# 这件事的平台人员看。在 2 下同意的同样要点一次「同意新说明」。
CONSENT_VERSION = 3
OUTPUTS_KEEP_DAYS = 90
OUTPUTS_EXPIRED = (
    "已按期清除：这位准专家上架或被下一位取代 90 天后，成稿与交回物不再保留"
)


class RuntimeIncubationIn(Strict):
    """knowbench 替用户提的孵化请求（请专家方案设计第 4.9 节第 6 条）：那句问题、
    从哪个项目哪件
    委托来、幂等钥匙；用户先让自己的模型直接答过的，附那份回答（只算提议）。"""

    owner_subject: str = Field(min_length=1, max_length=512)
    question: str = Field(min_length=1, max_length=2000)
    project_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, min_length=1, max_length=64)
    key: str = Field(min_length=8, max_length=128)
    reference_answer: str | None = Field(default=None, max_length=20000)


class KnowbotRequestIn(Strict):
    title: str = Field(min_length=1, max_length=80)
    idea: Idea
    runtime: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    cases: tuple[RunCase, ...] = Field(min_length=1, max_length=50)
    # 用户同意他为测试选定的资料用于测试（0027 决定 11 ④，第 3 版说明），不同意不受理
    consent_test_data: Literal[True]
    # 孵化自己来还是请平台（SDD 0046 第四节）；请平台的同时授权平台、设平台发起 AI 一轮
    # 的费用上限（元）
    mode: Literal["self", "platform"]
    budget_limit: Decimal | None = Field(default=None, gt=0, le=1000, decimal_places=2)

    @model_validator(mode="after")
    def _unique_cases(self) -> KnowbotRequestIn:
        ids = [c.case_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        if (self.mode == "platform") != (self.budget_limit is not None):
            raise ValueError("a budget limit goes with asking the platform, only")
        return self


# 与 knowmart 交付时的上限一致（审读第 10 条）：草稿测过了、交付才被拒，白测一轮
MAX_MANIFEST_BYTES = 1_000_000
MAX_RULE_SOURCES = 500


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
    if doc.get("schema") not in (MANIFEST_SCHEMA, DYNAMIC_MANIFEST_SCHEMA):
        return ["schema must be knowbot-manifest/v2 or knowbot-manifest/v3"]
    dynamic = doc["schema"] == DYNAMIC_MANIFEST_SCHEMA
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
    elif dynamic:
        if (
            pack.get("runtime_contract")
            not in (
                "knowbench/dynamic-methods/v1",
                "knowbench/dynamic-methods/v2",
                "knowbench/dynamic-methods/v3",
            )
            or "workflow" in pack
        ):
            problems.append("v3 requires a dynamic catalog, without workflow")
        if not isinstance(pack.get("method_units"), list) or not pack["method_units"]:
            problems.append("pack.method_units must be a non-empty list")
        if str(pack.get("version")) != version:
            problems.append("pack.version must equal the version")
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
    if "data_needs" in doc:
        # 清单 v2 = 逻辑 + 作者挑选的数据（decisions ⑭）：要什么数据每件委托由
        # 第 0 步按问题提出；要提示模型，写在步骤说明里
        problems.append(
            "a v2 manifest declares no data needs (say it in the step instead)"
        )
    if "data_bindings" in doc:
        problems.append("a v2 manifest has no data_bindings (test sets are separate)")
    if "bindings" in doc:
        problems.append("bindings are frozen at delivery, not written in a draft")
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
