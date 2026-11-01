"""创意到专家给两端的样子（SDD 0037）。

用户看的只给他要的：规则名（C-L5 这类）、run、平台的错、谁做的都不给；管理端看全的。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.application.ports.knowbots import (
    AcceptanceRecord,
    AmendmentRecord,
    DeliveryRecord,
    DraftRecord,
    ExpertRecord,
    GrantRecord,
    IncubationRecord,
    ProposalRecord,
    QuestionRecord,
    RequestRecord,
    RequestVersionRecord,
    TestOutputRecord,
    TestRecord,
    WorkdraftRecord,
)
from app.application.services.knowbot_board import Life, start_blocker, summary
from app.application.services.knowbot_experts import stage_of
from app.domain.knowbots.delivery import basis as delivery_basis
from app.domain.knowbots.drafts import CONSENT_VERSION
from app.domain.knowbots.lifecycle import ACTIVE_TESTS, draft_label, review_state
from app.domain.knowbots.platform_grants import basis as grant_basis
from app.domain.knowbots.platform_grants import state as grant_state
from app.domain.knowbots.workdraft import empty_body


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
        # formal：正式受理；self：「自助（不受理）」（SDD 0046 第二节）
        "kind": found.kind,
    }
    if admin:
        view |= {"checks": found.checks, "decided_by": found.decided_by}
    return view


def grant_view(found: GrantRecord, now: datetime, *, admin: bool) -> dict[str, Any]:
    """作者给平台的一张授权（SDD 0046 第四节）：现在的状态（过了期的算到期）、上限、
    期限、运营的结论。"""
    view: dict[str, Any] = {
        "grant_id": str(found.id),
        "kind": found.kind,
        "draft_id": None if found.draft_id is None else str(found.draft_id),
        "state": grant_state(found.status, found.expires_at, now),
        "budget_limit": f"{found.budget_limit:.2f}",
        "granted_at": iso(found.granted_at),
        "expires_at": iso(found.expires_at),
        "decided_at": iso(found.decided_at),
        "reason": found.reason,
        "ended_at": iso(found.ended_at),
        "basis": grant_basis(found.kind, found.id),
    }
    if admin:
        view |= {"decided_by": found.decided_by, "ended_by": found.ended_by}
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
        "mode": draft.mode,
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
        # 自助（用户本人在他自己的电脑上跑）还是平台；带的测试集第几版
        # （SDD 0046 第三节）
        "mode": test.mode,
        "test_set_version": test.test_set_version,
        # 测试证据进了作者产出区的那一份（0043 第 4.2 节）
        "evidence": test.evidence if (test.evidence or {}).get("material_id") else None,
    }
    if admin and test.mode == "self":
        # 自助测的：平台的人看不到测试集、用例、报告（SDD 0046 第三节）
        view["report"] = None
        return view
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
        # knowmart 那头这一版现在怎样：在售 active、待处理 held（所绑数据失效，别人请
        # 不了）与原因、停用 stopped（0043 第 4.3 节；后台每分钟取回）
        "market_status": (expert.delivery or {}).get("version_status"),
        "held_reason": (expert.delivery or {}).get("held_reason"),
        "stopped_at": iso(expert.stopped_at),
        "stop_reason": expert.stop_reason,
        # 试行期上架：两道门之前交付
        "trial": expert.trial,
        # 平台孵化、平台测的要平台先交回；测试通过的那一次是自助还是平台测的
        "needs_return": expert.needs_return,
        "test_mode": None if test is None else test.mode,
        # 交付过程（SDD 0046 第五节）：新的在前
        "deliveries": [
            delivery_view(d, life, admin=admin)
            for d in reversed(life.deliveries_of(expert.id))
        ],
    }
    if admin:
        view |= {
            "returned_by": expert.returned_by,
            "user_decided_by": expert.user_decided_by,
            "reviewed_by": expert.reviewed_by,
            "stopped_by": expert.stopped_by,
            "digest": expert.digest,
            "delivery_error": expert.delivery_error,
            "owner_subject": life.request.owner_subject,
        }
    return view


def delivery_view(found: DeliveryRecord, life: Life, *, admin: bool) -> dict[str, Any]:
    """一次交付（SDD 0046 第五节）：作者与审核员都看得到进度——自动查的结果（哪条用例、
    哪个词，不带原文）、许可预查、重跑的结论（审核员看结果、不看原始数据）、审核结论、
    进没进 knowmart 小店。"""
    rerun = life.test(found.rerun_test_id)
    check = found.binding_check or {}
    view: dict[str, Any] = {
        "delivery_id": str(found.id),
        "listing": found.listing,
        "conversion": found.conversion,
        "test_mode": found.test_mode,
        "needs_rerun": found.needs_rerun,
        "budget_limit": None
        if found.budget_limit is None
        else f"{found.budget_limit:.2f}",
        "status": found.status,
        "submitted_at": iso(found.submitted_at),
        "auto_check": found.auto_check,
        "binding_problem": check.get("problem"),
        "bindings": [
            {
                "material_id": b.get("material_id"),
                "version_no": b.get("version_no"),
                "license": b.get("license"),
            }
            for b in found.bindings
        ],
        "rerun": None
        if rerun is None
        else {
            "test_id": str(rerun.id),
            "number": rerun.number,
            "status": rerun.status,
            "report": _report(rerun),
        },
        "decided_at": iso(found.decided_at),
        "reason": found.reason,
        "category": found.category,
        "delivered_at": iso(found.delivered_at),
        "listing_status": (found.receipt or {}).get("listing_status"),
        "withdrawn_at": iso(found.withdrawn_at),
        "withdraw_reason": found.withdraw_reason,
        # 作者授权审核员看这次交付的依据；审完撤销（consent_ended_at）
        "basis": delivery_basis("review", found.id),
        "consent_ended_at": iso(found.consent_ended_at),
    }
    if admin:
        view |= {
            "submitted_by": found.submitted_by,
            "decided_by": found.decided_by,
            "withdrawn_by": found.withdrawn_by,
            "delivery_error": found.delivery_error,
            "binding_check": found.binding_check,
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
        # 现在的孵化是自助还是平台（web端孵化栏按它分，SDD 0046 第六节）
        "incubation_mode": request.incubation_mode,
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


def question_view(
    q: QuestionRecord,
    draft: DraftRecord | None,
    request: RequestRecord,
    *,
    admin: bool,
) -> dict[str, Any]:
    """一个待答的问题。孵化的缺口（kind gap）不挂准专家：label、draft_id 是空的，带 AI
    附的提议与是哪一轮（运营看不到缺口，只有作者本人这里有）。"""
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
        "label": None if draft is None else draft_label(draft.version, draft.revision),
        # 是哪位准专家的（web端按它对上，不按同名标题猜；SDD 0037）
        "draft_id": None if draft is None else str(draft.id),
        # 从哪来：测试中的，还是孵化的缺口（专家孵化方案第 8.4 节）
        "source": "incubation" if q.kind == "gap" else "test",
        "proposal": q.proposal,
        "incubation_id": None if q.incubation_id is None else str(q.incubation_id),
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
        "draft_id": None if q.draft_id is None else str(q.draft_id),
        "source": "incubation" if q.kind == "gap" else "test",
        "proposal": q.proposal,
    }
    if admin:
        view["route_reason"] = q.route_reason
    return view


def workdraft_view(
    request: RequestRecord,
    workdraft: WorkdraftRecord | None,
    origins: dict[str, str] | None = None,
) -> dict[str, Any]:
    """工作稿（作者本人看）：方法层的格子、每格的出处与它是怎么来的（作者写的、导入、
    复制……，页面在格子旁小字标出）、revision。还没存过的是空的一份。"""
    if workdraft is None:
        return {
            "request_id": str(request.id),
            "title": request.title,
            "revision": 0,
            "body": empty_body(),
            "sources": {},
            "origins": {},
            "data_formats": {},
            "updated_at": None,
        }
    return {
        "request_id": str(request.id),
        "title": request.title,
        "revision": workdraft.revision,
        "body": workdraft.body,
        "sources": workdraft.sources,
        "origins": origins or {},
        # 挑的数据的格式（挑的时候记下的，页面显示名字与描述；只是格式，不是内容）
        "data_formats": {
            pick: workdraft.data_formats[pick]
            for pick in (workdraft.body.get("data_picks") or [])
            if pick in workdraft.data_formats
        },
        "updated_at": iso(workdraft.updated_at),
    }


def proposal_view(found: ProposalRecord, *, admin: bool) -> dict[str, Any]:
    """运营的一条提议（形状同 AI 的建议）：改哪一格、怎么改、理由、作者怎么处理的。"""
    view: dict[str, Any] = {
        "proposal_id": str(found.id),
        "path": found.path,
        "op": found.op,
        "value": found.value,
        "reason": found.reason,
        "state": found.state,
        "proposed_at": iso(found.proposed_at),
        "decided_at": iso(found.decided_at),
    }
    if admin:
        view["proposed_by"] = found.proposed_by
    return view


def _suggestions(record: IncubationRecord) -> list[dict[str, Any]]:
    """这一轮的每条建议：改哪一格、怎么改、理由、出处（引到的那几行）与核得上没有、作者
    怎么处理的。"""
    handback = record.handback or {}
    sources = [s for s in handback.get("sources") or [] if isinstance(s, dict)]
    checked = record.review or {}
    out: list[dict[str, Any]] = []
    for s in handback.get("suggestions") or []:
        if not isinstance(s, dict):
            continue
        path, sid = str(s.get("path") or ""), str(s.get("id") or "")
        found = checked.get(sid) or {}
        decision = record.decisions.get(sid) or {}
        out.append(
            {
                "id": sid,
                "path": path,
                "op": s.get("op"),
                "value": s.get("value"),
                "reason": s.get("reason"),
                "sources": [
                    e
                    for e in sources
                    if e.get("path") == path
                    or str(e.get("path") or "").startswith(path + "/")
                    or path.startswith(str(e.get("path") or "") + "/")
                ],
                "kinds": found.get("kinds") or ["proposal"],
                "verified": bool(found.get("verified")),
                "problems": found.get("problems") or [],
                "state": decision.get("state") or "pending",
            }
        )
    return out


def _gap(gap: dict[str, Any], asked: QuestionRecord | None) -> dict[str, Any]:
    """一个缺口：问题、提议、选项，与进了问题表的那一条（审核栏里答）答了没有。"""
    return {
        "gap_id": gap.get("id"),
        "question": gap.get("question"),
        "proposal": gap.get("proposal"),
        "options": gap.get("options") or [],
        "question_id": None if asked is None else str(asked.id),
        "answered": asked is not None and asked.answer is not None,
        "answer": None if asked is None else asked.answer,
    }


def incubation_view(
    record: IncubationRecord, gaps: Sequence[QuestionRecord] | None = None
) -> dict[str, Any]:
    """AI 孵化的一轮（作者本人看，专家孵化方案第 8.2–8.4 节）：第几轮、动作与那句指令、
    状态、花费与上限、在等什么、没成的原因；给了缺口（gaps）的再带交回的建议、缺口（问题
    编号、答了没有）、自查。"""
    view: dict[str, Any] = {
        "incubation_id": str(record.id),
        "round": record.round,
        "mode": record.mode,
        "action": record.action,
        "instruction": record.instruction,
        "step_id": record.step_id,
        "status": record.status,
        "budget_limit": record.budget_limit,
        "spent": record.spent,
        "currency": "CNY",
        "waiting_for": record.waiting_for,
        "error": record.error,
        "environment_id": record.environment_id,
        "workspace_root": record.workspace_root,
        "request_version": record.request_version,
        "workdraft_revision": record.workdraft_revision,
        "started_at": iso(record.started_at),
        "finished_at": iso(record.finished_at),
    }
    if gaps is None:
        return view
    handback = record.handback or {}
    asked = {q.question_id: q for q in gaps}
    view["suggestions"] = _suggestions(record)
    view["gaps"] = [
        _gap(g, asked.get(str(g.get("id"))))
        for g in handback.get("gaps") or []
        if isinstance(g, dict)
    ]
    view["self_check"] = list(handback.get("self_check") or [])
    return view
