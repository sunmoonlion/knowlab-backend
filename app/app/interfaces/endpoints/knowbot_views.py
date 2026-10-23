"""创意到专家给两端的样子（SDD 0037）。

用户看的只给他要的：规则名（C-L5 这类）、run、平台的错、谁做的都不给；管理端看全的。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.application.ports.knowbots import (
    AcceptanceRecord,
    AmendmentRecord,
    DatasetRecord,
    DraftRecord,
    ExpertRecord,
    QuestionRecord,
    RequestRecord,
    RequestVersionRecord,
    TestOutputRecord,
    TestRecord,
)
from app.application.services.knowbot_board import Life, start_blocker, summary
from app.application.services.knowbot_experts import stage_of
from app.domain.knowbots.drafts import CONSENT_VERSION
from app.domain.knowbots.lifecycle import ACTIVE_TESTS, draft_label, review_state


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def acceptance_view(found: AcceptanceRecord | None, *, admin: bool) -> Any:
    if found is None:
        return None
    view: dict[str, Any] = {
        "accepted": found.accepted,
        "reason": found.reason,
        "decided_at": iso(found.decided_at),
        "trial": found.trial,
    }
    if admin:
        view |= {"checks": found.checks, "decided_by": found.decided_by}
    return view


def version_view(
    found: RequestVersionRecord, life: Life, *, admin: bool
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "version": found.version,
        "idea": found.idea,
        "cases": found.cases,
        "consent_version": found.consent_version,
        "created_at": iso(found.created_at),
        "acceptance": acceptance_view(life.acceptances.get(found.version), admin=admin),
    }
    if admin:
        view["created_by"] = found.created_by
    return view


def _rule_sources(draft: DraftRecord, *, admin: bool) -> list[Any]:
    raw = draft.manifest.get("rule_sources", [])
    sources = list(raw) if isinstance(raw, list) else []
    if admin:
        return sources
    # 给用户看的两项：哪条规则、出自你的哪句话（「我的话用在了哪」）
    return [
        {"rule": str(s.get("rule") or ""), "ref": str(s.get("ref") or "")}
        for s in sources
        if isinstance(s, dict)
    ]


def _user_review(draft: DraftRecord) -> Any:
    review = draft.user_review
    if not review:
        return None
    return {
        "state": review_state(review),
        "note": review.get("note"),
        "requested_at": review.get("requested_at"),
        "answered_at": review.get("answered_at"),
    }


def draft_view(draft: DraftRecord, life: Life, *, admin: bool) -> dict[str, Any]:
    view: dict[str, Any] = {
        "draft_id": str(draft.id),
        "label": draft_label(draft.version, draft.revision),
        "version": draft.version,
        "revision": draft.revision,
        "request_version": draft.request_version,
        "status": draft.status,
        "name": draft.manifest.get("name"),
        "created_at": iso(draft.created_at),
        "user_review": _user_review(draft),
        "rule_sources": _rule_sources(draft, admin=admin),
    }
    if admin:
        view |= {
            "rules_reviewed_by": draft.rules_reviewed_by,
            "rules_reviewed_at": iso(draft.rules_reviewed_at),
            "requested_by": (draft.user_review or {}).get("requested_by"),
            "blocked": start_blocker(life, draft)
            if draft.status == "pending_test"
            else None,
        }
    return view


def _report(test: TestRecord) -> dict[str, Any] | None:
    if test.report is None:
        return None
    return {
        "status": test.report.get("status"),
        "cases": test.report.get("cases", []),
        "cost": test.report.get("cost"),
        "finished_at": test.report.get("finished_at"),
    }


def test_view(test: TestRecord, life: Life, *, admin: bool) -> dict[str, Any]:
    draft = life.draft(test.draft_id)
    view: dict[str, Any] = {
        "test_id": str(test.id),
        "number": test.number,
        "draft_id": str(test.draft_id),
        "label": None if draft is None else draft_label(draft.version, draft.revision),
        "status": test.status,
        "started_at": iso(test.started_at),
        "finished_at": iso(test.finished_at),
        "report": _report(test),
        # 0012 之前测的（从准专家上搬来的那一次）：再之前的没留记录
        "backfilled": test.started_by is None and test.budget_limit is None,
    }
    if admin:
        active = test.status in ACTIVE_TESTS
        report = test.report or {}
        view |= {
            "run_id": test.run_id,
            "budget_limit": test.budget_limit,
            "timeout_seconds": test.timeout_seconds,
            "started_by": test.started_by,
            "error": test.error,
            # 在跑的走到哪了（账 135）、运行方怎么说（账 136、141）
            "progress": report.get("progress") if active else None,
            "run_status": report.get("status") if active else None,
            "run_running_since": report.get("started_at") if active else None,
            "run_blocked": report.get("blocked") if active else None,
        }
    return view


def expert_view(expert: ExpertRecord, life: Life, *, admin: bool) -> dict[str, Any]:
    draft = life.draft(expert.draft_id)
    test = life.test(expert.test_id)
    view: dict[str, Any] = {
        "expert_id": str(expert.id),
        "version": expert.version,
        "name": None if draft is None else draft.manifest.get("name"),
        "stage": stage_of(expert),
        "draft_id": str(expert.draft_id),
        "label": None if draft is None else draft_label(draft.version, draft.revision),
        "test_id": None if test is None else str(test.id),
        "test_number": None if test is None else test.number,
        "created_at": iso(expert.created_at),
        "returned_at": iso(expert.returned_at),
        "user_accepted": expert.user_accepted,
        "user_decided_at": iso(expert.user_decided_at),
        "review_approved": expert.review_approved,
        "review_listing": expert.review_listing,
        "review_reason": expert.review_reason,
        "reviewed_at": iso(expert.reviewed_at),
        "listed_at": iso(expert.listed_at),
        "knowbot_id": expert.knowmart_knowbot_id,
        "listing_status": (expert.delivery or {}).get("listing_status"),
        "stopped_at": iso(expert.stopped_at),
        "stop_reason": expert.stop_reason,
        # 试行期上架：两道门之前交付
        "trial": expert.trial,
    }
    if admin:
        view |= {
            "returned_by": expert.returned_by,
            "user_decided_by": expert.user_decided_by,
            "reviewed_by": expert.reviewed_by,
            "stopped_by": expert.stopped_by,
            "digest": expert.digest,
            "delivery_error": expert.delivery_error,
        }
    return view


def life_view(life: Life, *, admin: bool) -> dict[str, Any]:
    request: RequestRecord = life.request
    view: dict[str, Any] = {
        "request_id": str(request.id),
        "title": request.title,
        "runtime": request.runtime,
        "created_at": iso(request.created_at),
        "latest_version": request.latest_version,
        "summary": summary(life, for_user=not admin),
        # 同意的是第几版测试数据说明；低于现行版的，产出不取不显示（SDD 0037）
        "consent_version": request.consent_version,
        "consent_current": CONSENT_VERSION,
    }
    if admin:
        view["owner_subject"] = request.owner_subject
    return view


def amendment_view(
    item: tuple[AmendmentRecord, str, int | None], *, admin: bool
) -> dict[str, Any]:
    """旧的补充方法（SDD 0035，已取消）：只读，留作出处可查。"""
    amendment, status, revision = item
    view: dict[str, Any] = {
        "amendment_id": str(amendment.id),
        "text": amendment.text,
        "created_at": iso(amendment.created_at),
        "status": status,
        "used_in_revision": revision,
        "declined_reason": amendment.declined_reason,
        "declined_at": iso(amendment.declined_at),
    }
    if admin:
        view["declined_by"] = amendment.declined_by
    return view


def outputs_view(
    test: TestRecord, found: Sequence[TestOutputRecord] | None, *, admin: bool
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "test_id": str(test.id),
        # 创意是在旧说明下同意的：不取、不显示，用户同意新说明后才有（审读 10-19）
        "consent_needed": found is None,
        "cases": [
            {
                "case_id": o.case_id,
                "fetched_at": iso(o.fetched_at),
                "note": o.note,
                "outputs": o.outputs,
            }
            for o in found or ()
        ],
    }
    if admin:
        view["run_id"] = test.run_id
    return view


def dataset_view(found: DatasetRecord) -> dict[str, Any]:
    return {
        "dataset_id": str(found.id),
        "name": found.name,
        "version": found.version,
        "content_type": found.content_type,
        "size_bytes": found.size_bytes,
        "sha256": found.sha256,
        "created_at": iso(found.created_at),
    }


def question_view(
    q: QuestionRecord, draft: DraftRecord, request: RequestRecord, *, admin: bool
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "question_id": str(q.id),
        "kind": q.kind,
        "text": q.text,
        "options": q.options,
        "allow_free_text": q.allow_free_text,
        "case_id": q.case_id,
        "step_id": q.step_id,
        "asked_at": q.asked_at,
        "title": request.title,
        "request_id": str(request.id),
        "label": draft_label(draft.version, draft.revision),
        # 是哪位准专家的（web端按它对上，不按同名标题猜；SDD 0037）
        "draft_id": str(draft.id),
    }
    if admin:
        view |= {
            "route": q.route,
            "route_reason": q.route_reason,
            "owner_subject": request.owner_subject,
            "last_error": q.last_error,
        }
    return view


def answered_view(q: QuestionRecord, *, admin: bool) -> dict[str, Any]:
    view: dict[str, Any] = {
        "question_id": str(q.id),
        "kind": q.kind,
        "case_id": q.case_id,
        "key": q.key,
        "text": q.text,
        "answer": q.answer,
        "answered_by": q.answered_by,
        "answered_at": iso(q.answered_at),
        "draft_id": str(q.draft_id),
    }
    if admin:
        view["route_reason"] = q.route_reason
    return view
