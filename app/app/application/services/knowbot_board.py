"""两端的栏、大类、详情页（SDD 0037 第七至第九节）。只读，不改任何东西。

一个名称的一生（Life）：创意各版本、受理单、准专家、测试、专家。两端的栏都从一生里推：

- 管理端（都是过程）：受理 · 孵化 · 测试 · 交付（含全部专家）；每栏数「等我们」的条数。
- web端：申请 · 审核（用户的待办箱）· 专家（只看交回了的）；审核数「要你做的」，专家数
  「新交回」。
- 详情页：顶上一行每个过程最新的一项，下面按时间排的记录。

运营只看得到作者授权给平台的那几步（SDD 0046 第四节，`operator_view`）：
- 作者请了平台孵化（还有效的孵化授权）：创意各版本、正式受理单、平台孵化的准专家与
  平台测试；
- 作者交平台测某一位准专家（测试授权，含测完的）：只有这一位准专家与平台测的那几次，
  看不到创意、之前的草稿与问答；
- 作者交审了的那一版（本人授权审核员看这次交付，SDD 0046 第五节）：这一版专家、它的
  准专家（清单）与测试（自助测的只有结论没有报告；平台重跑的有结论）；
- 其余（纯自助的名称、作者点了「我自己来」的）运营一律看不到，列表里没有、按编号取是
  404。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from app.application.errors.exceptions import NotFoundError
from app.application.ports.knowbots import (
    AcceptanceRecord,
    DeliveryRecord,
    DraftRecord,
    ExpertRecord,
    GrantRecord,
    KnowlabRepository,
    QuestionRecord,
    RequestRecord,
    RequestVersionRecord,
    TestRecord,
)
from app.application.services.knowbot_experts import shown_to_author, stage_of
from app.application.services.knowbot_grants import live_incubation, reviewing
from app.application.services.knowbot_service import parse_id
from app.domain.knowbots.lifecycle import (
    ACTIVE_TESTS,
    DELIVERY_CATEGORIES,
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
    grants: tuple[GrantRecord, ...] = ()
    deliveries: tuple[DeliveryRecord, ...] = ()

    def deliveries_of(self, expert_id: uuid.UUID) -> tuple[DeliveryRecord, ...]:
        """这一版专家的交付过程，按提交先后。"""
        return tuple(d for d in self.deliveries if d.expert_id == expert_id)

    def open_delivery(self, expert_id: uuid.UUID) -> DeliveryRecord | None:
        """还没走完的那一次（在审，或审过了还没交成）。"""
        return next(
            (
                d
                for d in self.deliveries_of(expert_id)
                if d.status == "reviewing"
                or (d.status == "approved" and d.delivered_at is None)
            ),
            None,
        )

    def draft(self, draft_id: uuid.UUID) -> DraftRecord | None:
        return next((d for d in self.drafts if d.id == draft_id), None)

    def test(self, test_id: uuid.UUID | None) -> TestRecord | None:
        return next((t for t in self.tests if t.id == test_id), None)

    def latest_accepted(self) -> int | None:
        found = [v for v, a in self.acceptances.items() if a.accepted]
        return max(found) if found else None

    def test_grant(self, draft_id: uuid.UUID) -> GrantRecord | None:
        """这位准专家最近一张测试授权（不接的不算）。"""
        found = [
            g
            for g in self.grants
            if g.kind == "test" and g.draft_id == draft_id and g.status != "declined"
        ]
        return found[-1] if found else None

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


def operator_view(life: Life, now: datetime) -> Life | None:
    """运营看到的一生（SDD 0046 第四节）；运营什么都看不到时是 None。

    孵化授权还有效：创意、正式受理单、平台模式的准专家与它们的平台测试。另外，作者交平台
    测的准专家（有测试授权的）与平台测它的那几次。自助的准专家、自助测试、自助受理单、
    请平台孵化之前没交的东西运营都看不到。
    """
    incubating = live_incubation(life.grants, now) is not None
    tested = {
        g.draft_id
        for g in life.grants
        if g.kind == "test" and g.status != "declined" and g.draft_id is not None
    }
    # 作者交审了、还在审、给审核员的授权还在的那几版：这一版的准专家（审核员要看
    # 清单）与测试、重跑（SDD 0046 第五节）。审完（过、不过、撤回）授权撤了，运营只留
    # 上架与停用要的那一行摘要，不再给清单与测试
    submitted = {
        e.draft_id
        for e in life.experts
        if any(reviewing(d) for d in life.deliveries_of(e.id))
    }
    drafts = tuple(
        d
        for d in life.drafts
        if (incubating and d.mode == "platform") or d.id in tested or d.id in submitted
    )
    delivered = any(
        e.listed_at is not None or life.open_delivery(e.id) is not None
        for e in life.experts
        if life.deliveries_of(e.id)
    )
    if not incubating and not drafts and not delivered:
        return None
    shown = {d.id for d in drafts}
    reviewed = {e.test_id for e in life.experts if e.draft_id in submitted}
    tests = tuple(
        t
        for t in life.tests
        if t.draft_id in shown and (t.mode in ("platform", "rerun") or t.id in reviewed)
    )
    return replace(
        life,
        # 来源、参考回答只给孵化授权在效的平台孵化（纯自助的作者的内容运营看不到）
        request=life.request
        if incubating
        else replace(life.request, origin=None, reference_answer=None),
        versions=life.versions if incubating else (),
        acceptances=(
            {v: a for v, a in life.acceptances.items() if a.kind == "formal"}
            if incubating
            else {}
        ),
        drafts=drafts,
        tests=tests,
        # 交付审完了的只留在审、审过待交、已上架的那几版（运营要审、重试、作废、停用）；
        # 不过、撤回而没上架的不再给
        experts=tuple(
            e
            for e in life.experts
            if e.draft_id in shown
            or e.listed_at is not None
            or life.open_delivery(e.id) is not None
        ),
        # 审完的交付只留结论那几样：绑了哪几份、许可预查、自动查都不再给
        deliveries=tuple(
            d
            if reviewing(d)
            else replace(d, bindings=[], binding_check=None, auto_check=None)
            for d in life.deliveries
        ),
    )


class KnowbotBoard:
    def __init__(
        self,
        repo: KnowlabRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.now = now

    async def lives(
        self, owner_subject: str | None = None, *, operator: bool = False
    ) -> list[Life]:
        """全部名称（给了所有者就只有他的），新的在前。operator：管理端看的（只有平台
        模式的那几步）。"""
        requests = (
            await self.repo.list_requests()
            if owner_subject is None
            else await self.repo.list_requests_for_owner(owner_subject)
        )
        lives = await self._assemble(requests)
        if not operator:
            return lives
        now = self.now()
        return [v for life in lives if (v := operator_view(life, now)) is not None]

    async def life(
        self,
        request_id: str,
        owner_subject: str | None = None,
        *,
        operator: bool = False,
    ) -> Life:
        """一个名称的一生；给了所有者，不是他的与没有的一样，都是 404。
        operator 同上。"""
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None or (
            owner_subject is not None and request.owner_subject != owner_subject
        ):
            raise NotFoundError("No such request", code="request_not_found")
        life = (await self._assemble([request]))[0]
        if not operator:
            return life
        shown = operator_view(life, self.now())
        if shown is None:
            raise NotFoundError("No such request", code="request_not_found")
        return shown

    async def platform_asks(self, life: Life) -> list[dict[str, Any]]:
        """平台在问作者什么（SDD 0046 第四节「平台视图：进度、在等你什么、运营问了
        什么」）：只列平台那一头来的，作者自助的不在里面。每条：种类、还在等作者（open）还是作者
        处理过了（done）、说了什么、什么时候、去哪处理要的编号；按时间先后。

        - review：平台人员请作者审核的准专家（作者说行 / 不行）；
        - expert：平台交回、等作者确认要不要的专家（先用户、后运营，0046 第五节）；
        - proposal：平台人员对工作稿一格的提议（作者接受了才算，专家孵化方案第十二节）；
        - gap：平台模式的 AI 一轮列出的缺口；
        - question：平台测试里转给作者答的问题（运营看过后转给作者，0046 第三节）。"""
        asks: list[dict[str, Any]] = []
        for draft in life.drafts:
            review = draft.user_review or {}
            state = review_state(draft.user_review)
            if state is None:
                continue
            asks.append(
                {
                    "kind": "review",
                    "state": "open" if state == "pending" else "done",
                    "at": _iso(review.get("requested_at")),
                    "draft_id": str(draft.id),
                    "label": draft_label(draft.version, draft.revision),
                    "answer": None if state == "pending" else state,
                }
            )
        for expert in life.experts:
            if not expert.needs_return or expert.returned_at is None:
                continue  # 只有平台交回的才是平台在问
            asks.append(
                {
                    "kind": "expert",
                    "state": "open" if stage_of(expert) == "awaiting_user" else "done",
                    "at": _iso(expert.returned_at),
                    "expert_id": str(expert.id),
                    "label": str(expert.version),
                    "answer": None
                    if expert.user_accepted is None
                    else ("accepted" if expert.user_accepted else "declined"),
                }
            )
        for proposal in await self.repo.list_proposals(life.request.id):
            asks.append(
                {
                    "kind": "proposal",
                    "state": "open" if proposal.state == "pending" else "done",
                    "at": _iso(proposal.proposed_at),
                    "text": proposal.reason,
                    "path": proposal.path,
                    "answer": None if proposal.state == "pending" else proposal.state,
                }
            )
        platform_rounds = {
            r.id
            for r in await self.repo.list_incubations(request_id=life.request.id)
            if r.mode == "platform"
        }
        for gap in await self.repo.list_gaps(request_ids=[life.request.id]):
            if gap.incubation_id not in platform_rounds:
                continue
            asks.append(_question_ask("gap", gap))
        platform_runs = {t.run_id for t in life.tests if t.mode == "platform"}
        for draft in life.drafts:
            for question in await self.repo.list_questions(draft_id=draft.id):
                if question.run_id in platform_runs and question.route == "user":
                    asks.append(_question_ask("question", question))
        return sorted(asks, key=lambda a: a.get("at") or "")

    async def _assemble(self, requests: Sequence[RequestRecord]) -> list[Life]:
        ids = [r.id for r in requests]
        if not ids:
            return []
        versions = await self.repo.list_versions(ids)
        acceptances = await self.repo.list_acceptances(ids)
        drafts = await self.repo.list_drafts(request_ids=ids)
        tests = await self.repo.list_tests(request_ids=ids)
        experts = await self.repo.list_experts(ids)
        grants = await self.repo.list_grants(ids)
        deliveries = await self.repo.list_deliveries(ids)
        return [
            Life(
                request=r,
                versions=tuple(v for v in versions if v.request_id == r.id),
                acceptances={a.version: a for a in acceptances if a.request_id == r.id},
                drafts=tuple(d for d in drafts if d.request_id == r.id),
                tests=tuple(t for t in tests if t.request_id == r.id),
                experts=tuple(e for e in experts if e.request_id == r.id),
                grants=tuple(g for g in grants if g.request_id == r.id),
                deliveries=tuple(d for d in deliveries if d.request_id == r.id),
            )
            for r in requests
        ]


# ---------- 管理端四栏 ----------
def acceptance_items(lives: Sequence[Life]) -> list[tuple[str, Life, int]]:
    """受理栏的正式受理：待受理（请了平台孵化、最新版本还没正式受理单）/ 已受理 /
    不受理。lives 是运营看到的（operator_view 已滤掉自助受理单与没请平台的名称）。"""
    out: list[tuple[str, Life, int]] = []
    for life in lives:
        if not life.versions:
            continue  # 只交平台测的：没有创意可受理
        latest = life.request.latest_version
        if latest not in life.acceptances:
            out.append(("pending", life, latest))
        for version, found in sorted(life.acceptances.items(), reverse=True):
            out.append(("accepted" if found.accepted else "rejected", life, version))
    return out


def light_items(
    lives: Sequence[Life],
) -> list[tuple[str, Life, DraftRecord, GrantRecord]]:
    """受理栏的轻受理：作者交平台测的准专家（待轻受理 / 已接 / 不接）。"""
    out: list[tuple[str, Life, DraftRecord, GrantRecord]] = []
    for life in lives:
        for grant in reversed(life.grants):
            if grant.kind != "test" or grant.draft_id is None:
                continue
            draft = life.draft(grant.draft_id)
            if draft is None:
                continue
            category = {"pending": "light_pending", "declined": "light_declined"}.get(
                grant.status, "light_accepted"
            )
            out.append((category, life, draft, grant))
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


def delivery_category(life: Life, expert: ExpertRecord) -> str:
    """交付栏的大类：有没走完的交付（含自用转公开）的算待审核；其余按阶段。"""
    if life.open_delivery(expert.id) is not None:
        return "to_review"
    stage = stage_of(expert)
    return category_of(stage, DELIVERY_CATEGORIES) or stage


def delivery_items(lives: Sequence[Life]) -> list[tuple[str, Life, ExpertRecord]]:
    """交付栏：待交回 / 等用户提交 / 待运营审核（含自动查不过、等重跑、交付失败待重试、
    自用转公开）/ 已上架 / 没上架；停用了的只在「全部专家」里。全部专家就是这里的每一
    项。lives 是运营看到的（作者没交审的自助版本不在里面）。"""
    out: list[tuple[str, Life, ExpertRecord]] = []
    for life in lives:
        for expert in reversed(life.experts):
            out.append((delivery_category(life, expert), life, expert))
    return out


def _ours(life: Life, expert: ExpertRecord) -> bool:
    """交付栏「等我们」：待交回、在审（含自用转公开）、审过了还没交成（含转公开的）。"""
    return stage_of(expert) in ("to_return", "to_deliver") or (
        life.open_delivery(expert.id) is not None
    )


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
        "delivery": sum(_ours(life, e) for life in lives for e in life.experts),
    }


# ---------- web端三栏 ----------
def application_category(life: Life) -> str:
    """申请栏：没受理（最新版本不受理）/ 已出专家（交回过）/ 在做。"""
    latest = life.acceptances.get(life.request.latest_version)
    if latest is not None and not latest.accepted:
        return "rejected"
    if any(shown_to_author(e) for e in life.experts):
        return "has_expert"
    return "working"


def inbox_items(
    lives: Sequence[Life], questions: Sequence[QuestionRecord]
) -> list[tuple[str, str, Life, Any]]:
    """审核栏：（todo / done，question / draft / expert，名称，那一项）。

    todo 是要用户做的：转给他的问题与孵化缺口、请他审核的准专家、交回等他定的专家；
    done 是答过、定过的记录。
    """
    by_draft = {d.id: life for life in lives for d in life.drafts}
    by_request = {life.request.id: life for life in lives}
    out: list[tuple[str, str, Life, Any]] = []
    for question in questions:
        life = (
            by_request.get(question.request_id)
            if question.draft_id is None and question.request_id is not None
            else by_draft.get(question.draft_id)
            if question.draft_id is not None
            else None
        )
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
            if not shown_to_author(expert):
                continue
            if stage_of(expert) == "awaiting_user":
                out.append(("todo", "expert", life, expert))
            elif expert.user_decided_at is not None:
                out.append(("done", "expert", life, expert))
    return out


def user_expert_items(lives: Sequence[Life]) -> list[tuple[str, Life, ExpertRecord]]:
    """专家栏：只看看得到的（要交回的交回了才算）。待你提交 / 在审 / 能用 / 停用了 /
    没上架。"""
    out: list[tuple[str, Life, ExpertRecord]] = []
    for life in lives:
        for expert in reversed(life.experts):
            if not shown_to_author(expert):
                continue
            category = category_of(stage_of(expert), USER_EXPERT_CATEGORIES)
            if category is not None:
                out.append((category, life, expert))
    return out


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) and value else None


def _question_ask(kind: str, question: QuestionRecord) -> dict[str, Any]:
    """平台在问作者的一个问题：问了什么、作者答了什么（选项的字、自己写的）。"""
    said: str | None = None
    if question.answer is not None:
        answer = question.answer
        option = answer.get("option_id")
        label = next(
            (str(o.get("label")) for o in question.options if o.get("id") == option),
            "按提议" if option == "proposal" else str(option or ""),
        )
        parts = [
            label,
            str(answer.get("decision") or ""),
            str(answer.get("text") or ""),
        ]
        said = "；".join(x for x in parts if x) or None
    return {
        "kind": kind,
        "state": "open" if question.answer is None else "done",
        "at": question.asked_at or _iso(question.answered_at),
        "question_id": str(question.id),
        "text": question.text,
        "answer": said,
    }


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
        if for_user and not shown_to_author(e):
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
        for d in life.deliveries_of(e.id):
            dref = {**refs, "delivery_id": str(d.id), "listing": d.listing}
            add(d.submitted_at, "delivery_submitted", d.submitted_by, **dref)
            add(
                d.decided_at,
                "delivery_decided",
                d.decided_by,
                approved=d.status == "approved",
                **dref,
            )
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
    experts = [e for e in life.experts if not for_user or shown_to_author(e)]
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
