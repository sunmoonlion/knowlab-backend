"""两端的栏、大类、详情页（SDD 0037 第七至第九节）。只读，不改任何东西。

一个名称的一生（Life）：创意各版本、受理单、准专家、测试、专家。两端的栏都从一生里推：

- 管理端（都是过程）：受理 · 孵化 · 测试 · 交付（含全部专家）；每栏数「等我们」的条数。
- web端：申请 · 审核（用户的待办箱）· 专家（只看交回了的）；审核数「要你做的」，专家数
  「新交回」。
- 详情页：顶上一行每个过程最新的一项，下面按时间排的记录。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.application.errors.exceptions import NotFoundError
from app.application.ports.knowbots import (
    AcceptanceRecord,
    DraftRecord,
    ExpertRecord,
    KnowlabRepository,
    QuestionRecord,
    RequestRecord,
    RequestVersionRecord,
    TestRecord,
)
from app.application.services.knowbot_experts import stage_of
from app.application.services.knowbot_service import parse_id
from app.domain.knowbots.lifecycle import (
    ACTIVE_TESTS,
    DELIVERY_CATEGORIES,
    OUR_EXPERT_STAGES,
    USER_EXPERT_CATEGORIES,
    category_of,
    draft_label,
    review_state,
)


@dataclass(frozen=True)
class Life:
    request: RequestRecord
    versions: tuple[RequestVersionRecord, ...] = ()
    acceptances: dict[int, AcceptanceRecord] = field(default_factory=dict)
    drafts: tuple[DraftRecord, ...] = ()
    tests: tuple[TestRecord, ...] = ()
    experts: tuple[ExpertRecord, ...] = ()

    def draft(self, draft_id: uuid.UUID) -> DraftRecord | None:
        return next((d for d in self.drafts if d.id == draft_id), None)

    def test(self, test_id: uuid.UUID | None) -> TestRecord | None:
        return next((t for t in self.tests if t.id == test_id), None)

    def latest_accepted(self) -> int | None:
        found = [v for v, a in self.acceptances.items() if a.accepted]
        return max(found) if found else None

    def expert_versions(self) -> set[str]:
        return {str(e.version) for e in self.experts}

    def draft_alive(self, draft: DraftRecord) -> bool:
        """还能往下走的准专家：待测（用户没说不行、版本号没被占）、测试中、通过。"""
        if draft.status == "passed" or draft.status == "testing":
            return True
        return (
            draft.status == "pending_test"
            and review_state(draft.user_review) != "not_ok"
            and draft.version not in self.expert_versions()
        )

    def needs_incubation(self) -> bool:
        """最新受理了的版本还没有能往下走的准专家。"""
        version = self.latest_accepted()
        if version is None:
            return False
        return not any(
            d.request_version == version and self.draft_alive(d) for d in self.drafts
        )

    def active_test(self) -> TestRecord | None:
        return next((t for t in self.tests if t.status in ACTIVE_TESTS), None)


class KnowbotBoard:
    def __init__(self, repo: KnowlabRepository) -> None:
        self.repo = repo

    async def lives(self, owner_subject: str | None = None) -> list[Life]:
        """全部名称（给了所有者就只有他的），新的在前。"""
        requests = (
            await self.repo.list_requests()
            if owner_subject is None
            else await self.repo.list_requests_for_owner(owner_subject)
        )
        return await self._assemble(requests)

    async def life(self, request_id: str, owner_subject: str | None = None) -> Life:
        """一个名称的一生；给了所有者，不是他的与没有的一样，都是 404。"""
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None or (
            owner_subject is not None and request.owner_subject != owner_subject
        ):
            raise NotFoundError("No such request", code="request_not_found")
        return (await self._assemble([request]))[0]

    async def _assemble(self, requests: Sequence[RequestRecord]) -> list[Life]:
        ids = [r.id for r in requests]
        if not ids:
            return []
        versions = await self.repo.list_versions(ids)
        acceptances = await self.repo.list_acceptances(ids)
        drafts = await self.repo.list_drafts(request_ids=ids)
        tests = await self.repo.list_tests(request_ids=ids)
        experts = await self.repo.list_experts(ids)
        return [
            Life(
                request=r,
                versions=tuple(v for v in versions if v.request_id == r.id),
                acceptances={a.version: a for a in acceptances if a.request_id == r.id},
                drafts=tuple(d for d in drafts if d.request_id == r.id),
                tests=tuple(t for t in tests if t.request_id == r.id),
                experts=tuple(e for e in experts if e.request_id == r.id),
            )
            for r in requests
        ]


# ---------- 管理端四栏 ----------
def acceptance_items(lives: Sequence[Life]) -> list[tuple[str, Life, int]]:
    """受理栏：待受理（每个名称的最新版本还没受理单）/ 已受理 / 不受理。"""
    out: list[tuple[str, Life, int]] = []
    for life in lives:
        latest = life.request.latest_version
        if latest not in life.acceptances:
            out.append(("pending", life, latest))
        for version, found in sorted(life.acceptances.items(), reverse=True):
            out.append(("accepted" if found.accepted else "rejected", life, version))
    return out


def incubation_items(
    lives: Sequence[Life],
) -> list[tuple[str, Life, DraftRecord | None]]:
    """孵化栏：待孵化（名称）/ 孵化中（等用户审核）/ 已孵出（准专家）。"""
    out: list[tuple[str, Life, DraftRecord | None]] = []
    for life in lives:
        if life.needs_incubation():
            out.append(("todo", life, None))
        for draft in reversed(life.drafts):
            waiting = review_state(draft.user_review) == "pending"
            out.append(("in_progress" if waiting else "done", life, draft))
    return out


def start_blocker(life: Life, draft: DraftRecord) -> str | None:
    """待发起的准专家还差什么（None 就能发起）。"""
    if draft.version in life.expert_versions():
        return "version_taken"
    if not draft.rules_reviewed_by:
        return "rules_not_reviewed"
    if life.active_test() is not None:
        return "test_running"
    return None


def lab_test_items(
    lives: Sequence[Life],
) -> list[tuple[str, Life, DraftRecord | TestRecord]]:
    """测试栏：待发起（准专家）/ 排队 / 进行中 / 等用户答 / 已结束（测试）。"""
    out: list[tuple[str, Life, DraftRecord | TestRecord]] = []
    for life in lives:
        for draft in reversed(life.drafts):
            if draft.status == "pending_test" and review_state(
                draft.user_review
            ) not in ("pending", "not_ok"):
                out.append(("to_start", life, draft))
        for test in reversed(life.tests):
            category = test.status if test.status in ACTIVE_TESTS else "finished"
            out.append((category, life, test))
    return out


def delivery_items(lives: Sequence[Life]) -> list[tuple[str, Life, ExpertRecord]]:
    """交付栏：待交回 / 等用户同意 / 待运营审核（含交付失败待重试）/ 已上架 / 没上架；
    停用了的只在「全部专家」里。全部专家就是这里的每一项。"""
    out: list[tuple[str, Life, ExpertRecord]] = []
    for life in lives:
        for expert in reversed(life.experts):
            stage = stage_of(expert)
            out.append((category_of(stage, DELIVERY_CATEGORIES) or stage, life, expert))
    return out


def admin_counts(lives: Sequence[Life]) -> dict[str, int]:
    """每栏「等我们」的条数。"""
    return {
        "acceptance": sum(c == "pending" for c, _, _ in acceptance_items(lives)),
        "incubation": sum(c == "todo" for c, _, _ in incubation_items(lives)),
        "tests": sum(
            c == "to_start"
            and isinstance(item, DraftRecord)
            and start_blocker(life, item) != "version_taken"
            for c, life, item in lab_test_items(lives)
        ),
        "delivery": sum(
            stage_of(e) in OUR_EXPERT_STAGES for life in lives for e in life.experts
        ),
    }


# ---------- web端三栏 ----------
def application_category(life: Life) -> str:
    """申请栏：没受理（最新版本不受理）/ 已出专家（交回过）/ 在做。"""
    latest = life.acceptances.get(life.request.latest_version)
    if latest is not None and not latest.accepted:
        return "rejected"
    if any(e.returned_at is not None for e in life.experts):
        return "has_expert"
    return "working"


def inbox_items(
    lives: Sequence[Life], questions: Sequence[QuestionRecord]
) -> list[tuple[str, str, Life, Any]]:
    """审核栏：（todo / done，question / draft / expert，名称，那一项）。

    todo 是要用户做的：转给他的问题、请他审核的准专家、交回等他定的专家；done 是答过、
    定过的记录。
    """
    by_draft = {d.id: life for life in lives for d in life.drafts}
    out: list[tuple[str, str, Life, Any]] = []
    for question in questions:
        life = by_draft.get(question.draft_id)
        if life is not None:
            out.append(("todo", "question", life, question))
    for life in lives:
        for draft in reversed(life.drafts):
            state = review_state(draft.user_review)
            if state == "pending":
                out.append(("todo", "draft", life, draft))
            elif state is not None:
                out.append(("done", "draft", life, draft))
        for expert in reversed(life.experts):
            if expert.returned_at is None:
                continue
            if stage_of(expert) == "awaiting_user":
                out.append(("todo", "expert", life, expert))
            elif expert.user_decided_at is not None:
                out.append(("done", "expert", life, expert))
    return out


def user_expert_items(lives: Sequence[Life]) -> list[tuple[str, Life, ExpertRecord]]:
    """专家栏：只看交回了的。新交回 / 你已同意等我们审核 / 能用 / 停用了 / 没上架。"""
    out: list[tuple[str, Life, ExpertRecord]] = []
    for life in lives:
        for expert in reversed(life.experts):
            if expert.returned_at is None:
                continue
            category = category_of(stage_of(expert), USER_EXPERT_CATEGORIES)
            if category is not None:
                out.append((category, life, expert))
    return out


# ---------- 详情页 ----------
def _at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def timeline(life: Life, *, for_user: bool) -> list[dict[str, Any]]:
    """按时间排的记录：每条是哪个过程、输入、输出、谁（只给管理端）、何时。

    给用户的：没交回的专家不列；谁做的不列。
    """
    events: list[dict[str, Any]] = []

    def add(at: Any, kind: str, by: str | None = None, **refs: Any) -> None:
        when = _at(at)
        if when is None:
            return
        event: dict[str, Any] = {"at": when, "kind": kind, **refs}
        if not for_user:
            event["by"] = by
        events.append(event)

    for v in life.versions:
        add(v.created_at, "version", v.created_by, version=v.version)
    for version, a in life.acceptances.items():
        add(
            a.decided_at,
            "acceptance",
            a.decided_by,
            version=version,
            accepted=a.accepted,
            trial=a.trial,
        )
    labels = {d.id: draft_label(d.version, d.revision) for d in life.drafts}
    for d in life.drafts:
        add(
            d.created_at,
            "draft",
            None,
            draft_id=str(d.id),
            label=labels[d.id],
            request_version=d.request_version,
        )
        review = d.user_review or {}
        add(
            review.get("requested_at"),
            "review_requested",
            review.get("requested_by"),
            draft_id=str(d.id),
            label=labels[d.id],
        )
        add(
            review.get("answered_at"),
            "review_answered",
            None,
            draft_id=str(d.id),
            label=labels[d.id],
            ok=review.get("ok"),
        )
    for t in life.tests:
        refs = {"test_id": str(t.id), "number": t.number, "label": labels[t.draft_id]}
        add(t.started_at, "test_started", t.started_by, **refs)
        add(t.finished_at, "test_finished", None, status=t.status, **refs)
    for e in life.experts:
        if for_user and e.returned_at is None:
            continue
        test = life.test(e.test_id)
        refs = {"expert_id": str(e.id), "version": e.version}
        add(
            e.created_at,
            "expert",
            None,
            test_number=test.number if test else None,
            label=labels.get(e.draft_id),
            trial=e.trial,
            **refs,
        )
        add(e.returned_at, "returned", e.returned_by, **refs)
        add(
            e.user_decided_at,
            "user_decided",
            e.user_decided_by,
            accepted=e.user_accepted,
            **refs,
        )
        add(
            e.reviewed_at,
            "reviewed",
            e.reviewed_by,
            approved=e.review_approved,
            listing=e.review_listing,
            **refs,
        )
        add(e.listed_at, "listed", None, **refs)
        add(e.stopped_at, "stopped", e.stopped_by, **refs)
    events.sort(key=lambda event: event["at"])
    for event in events:
        event["at"] = event["at"].isoformat()
    return events


def summary(life: Life, *, for_user: bool) -> dict[str, Any]:
    """顶上一行：每个过程最新的一项。"""
    latest = life.request.latest_version
    acceptance = life.acceptances.get(latest)
    draft = life.drafts[-1] if life.drafts else None
    test = life.tests[-1] if life.tests else None
    experts = [e for e in life.experts if not for_user or e.returned_at is not None]
    expert = experts[-1] if experts else None
    return {
        "version": latest,
        "acceptance": None
        if acceptance is None
        else ("accepted" if acceptance.accepted else "rejected"),
        "draft": None if draft is None else draft_label(draft.version, draft.revision),
        "test": None
        if test is None
        else {"number": test.number, "status": test.status},
        "expert": None
        if expert is None
        else {"version": expert.version, "stage": stage_of(expert)},
    }
