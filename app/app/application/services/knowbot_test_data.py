"""测试里专家提出的数据（SDD 0046 第三节，decisions ⑭）：作者本人确认，钉成测试集。

运行方在测试委托里跑第 0 步（或运行中专家又要数据），提出的几样报成种类 `data` 的待回答
问题。这里：

1. 作者打开测试页时，凭他本人的令牌在 `app:knowlab` 的范围里解析
   （他放开给 knowlab 的、名下的引用、公开原件），列给他看；
2. 作者确认：能换一份、改主体或年份、去掉、自己加一样；缺的公开披露确认一次、
   交 knowfeed，测试页刷新时查进度，取齐了重解析接着往下；没取到的停下问他，
   照现有的接着做或停下测试；
3. 定下来的那几份，knowlab 签这条用例的测试运行授权（一条用例一张，不中途追加），连同
   钉住的几份回答那个问题（经问题服务送运行方，送不到下次刷新补送）；
4. 测试结束：签的授权撤掉；作者确认过的按用例记成测试集的新一版，重测带着它发起。

平台模式也一样由作者本人确认，运营不能替他确认（问题建出来就归用户）。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    KnowlabRepository,
    QuestionRecord,
    RequestRecord,
    TestRecord,
    TestSetRecord,
)
from app.domain.knowbots.data_plan import Plan, item_ref, plan
from app.domain.knowbots.data_request import DataRequest
from app.domain.knowbots.lifecycle import ACTIVE_TESTS
from app.domain.knowbots.test_run import CaseData

UserToken = Callable[[], Awaitable[str]]
ALL_FORMATS = ["pdf", "excel", "csv", "text", "markdown", "html"]
MAX_ITEMS = 20
OPEN_DEMAND = ("accepted", "fetching")
# 一条用例的测试运行授权：测试时限加最长等人的时间，再宽限一小时，不超过 14 天
GRANT_MAX_SECONDS = 14 * 24 * 3600
GRANT_MARGIN_SECONDS = 3600


class KnowbaseV2(Protocol):
    async def resolve(
        self,
        user_token: str,
        *,
        consumer: str,
        needs: list[dict[str, Any]],
        task_ref: str,
    ) -> dict[str, Any]: ...
    async def resolution(
        self, user_token: str, resolution_id: str
    ) -> dict[str, Any]: ...
    async def revise_needs(
        self, user_token: str, resolution_id: str, needs: list[dict[str, Any]]
    ) -> dict[str, Any]: ...
    async def confirm(
        self,
        user_token: str,
        *,
        items: list[dict[str, Any]],
        task_ref: str,
        resolution_id: str,
        revision: int,
    ) -> dict[str, Any]: ...
    async def refresh(self, subject: str, resolution_id: str) -> dict[str, Any]: ...
    async def case_grant(
        self, *, owner: str, ref: str, items: list[dict[str, Any]], ttl_seconds: int
    ) -> str: ...
    async def revoke_case_grant(self, grant_id: str) -> None: ...


class Supply(Protocol):
    def configured(self) -> bool: ...
    async def submit(
        self,
        *,
        owner: str,
        confirmation_id: str,
        idempotency_key: str,
        items: list[dict[str, Any]],
        task_ref: str | None,
        proposer: str = "user",
        chain: list[str] | None = None,
    ) -> dict[str, Any]: ...
    async def get(self, demand_id: str) -> dict[str, Any]: ...


class Deliverer(Protocol):
    async def deliver(
        self, run_id: str, request: RequestRecord, ids: list[uuid.UUID]
    ) -> None: ...


def _bad(message: str) -> BadRequestError:
    return BadRequestError(message, code="test_data_invalid")


def grant_ttl(test: TestRecord, max_wait_seconds: int) -> int:
    return min(
        GRANT_MAX_SECONDS,
        int(test.timeout_seconds or 3600) + max_wait_seconds + GRANT_MARGIN_SECONDS,
    )


def case_data(pins: list[dict[str, Any]], grant_id: str | None) -> dict[str, Any]:
    """测试契约 CaseData 的形状（没写的读法不带）。"""
    items = [{k: v for k, v in p.items() if v is not None} for p in pins]
    return CaseData.model_validate(
        {"grant_id": grant_id, "items": items} if items else {}
    ).model_dump(mode="json")


class KnowbotTestData:
    def __init__(
        self,
        repo: KnowlabRepository,
        knowbase: KnowbaseV2,
        supply: Supply | None,
        deliverer: Deliverer,
        *,
        supply_enabled: bool,
        max_wait_seconds: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.knowbase = knowbase
        self.supply = supply
        self.deliverer = deliverer
        # fail-closed：开关开着、knowfeed 也配好了才补采
        self.supply_enabled = bool(
            supply_enabled and supply is not None and supply.configured()
        )
        self.max_wait_seconds = max_wait_seconds
        self.now = now

    # ---------- 作者 ----------
    async def pending(
        self, owner_subject: str, test_id: str, user_token: UserToken
    ) -> list[dict[str, Any]]:
        """这次测试里等作者定的数据：每一样解析到哪、候选有哪些、在取的、没取到的。"""
        test, request = await self._mine(owner_subject, test_id)
        out = []
        for q in await self._data_questions(test):
            state = dict(q.data_state or {})
            if not state:
                state = await self._resolve(q, test, request, user_token)
            view: dict[str, Any] = {
                "question_id": str(q.id),
                "case_id": q.case_id,
                "status": state.get("status"),
                "needs": state.get("needs") or q.requests,
                "missing": state.get("missing") or [],
                "incomplete": state.get("incomplete") or [],
                "items": [],
            }
            if state.get("status") == "proposed":
                latest = await self.knowbase.resolution(
                    await user_token(), str(state["resolution_id"])
                )
                view["items"] = [
                    {**i, "ref": item_ref(i)} for i in latest.get("items") or []
                ]
            out.append(view)
        return out

    async def confirm(
        self,
        owner_subject: str,
        question_id: str,
        user_token: UserToken,
        *,
        picks: dict[str, dict[str, Any]] | None = None,
        dropped: list[str] | None = None,
        needs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        q, test, request = await self._open(owner_subject, question_id)
        state = dict(q.data_state or {})
        if state.get("status") != "proposed":
            raise ConcurrencyConflictError(
                "Nothing is waiting for your confirmation", code="test_data_state"
            )
        token = await user_token()
        asked = _needs(state.get("needs") or q.requests)
        resolution_id = str(state["resolution_id"])
        if needs is not None:
            asked = _revised(needs, q.case_id)
            latest = await self.knowbase.revise_needs(
                token, resolution_id, [n.model_dump(mode="json") for n in asked]
            )
            state["needs"] = [n.model_dump(mode="json") for n in asked]
        else:
            latest = await self.knowbase.resolution(token, resolution_id)
        draft = plan(
            latest,
            asked,
            picks=picks or {},
            dropped=dropped or [],
            supply_enabled=self.supply_enabled,
        )
        if draft.problems:
            raise _bad("; ".join(draft.problems))
        state.update(
            {
                "revision": int(latest["revision"]),
                "picks": picks or {},
                "dropped": dropped or [],
                "incomplete": draft.incomplete,
            }
        )
        if draft.confirm and self.supply is not None:
            confirmed = await self.knowbase.confirm(
                token,
                items=[
                    {"key": c["key"], "need_key": c["need_key"]} for c in draft.confirm
                ],
                task_ref=_task_ref(test, q),
                resolution_id=resolution_id,
                revision=int(latest["revision"]),
            )
            supply = [
                b["item_id"]
                for b in confirmed.get("branches") or []
                if b.get("branch") == "supply"
            ]
            if supply:
                by_id = {str(i.get("item_id")): i for i in confirmed.get("items") or []}
                demand = await self.supply.submit(
                    owner=owner_subject,
                    confirmation_id=str(confirmed["confirmation_id"]),
                    idempotency_key=f"kl-{q.id}"[:128],
                    task_ref=_task_ref(test, q),
                    proposer="expert",
                    chain=[f"knowlab-test:{request.id}", str(test.id)],
                    items=[
                        {
                            "confirmation_item": i,
                            "key": by_id[i]["key"],
                            "readings": [str(by_id[i].get("reading") or "original")],
                        }
                        for i in supply
                    ],
                )
                state.update(
                    {
                        "status": "supplying",
                        "confirmation_id": str(confirmed["confirmation_id"]),
                        "demand_id": str(demand["demand_id"]),
                        "waiting_for": [c["label"] for c in draft.confirm],
                    }
                )
                async with self.repo.transaction():
                    await self.repo.update_question(q.id, data_state=state)
                return {"status": "supplying", "waiting_for": state["waiting_for"]}
            latest = await self.knowbase.refresh(owner_subject, resolution_id)
            draft = plan(
                latest,
                asked,
                picks=picks or {},
                dropped=dropped or [],
                after_supply=True,
            )
        await self._answer(q, test, request, state, draft)
        return {"status": "answered", "pinned": len(draft.pins)}

    async def decline(self, owner_subject: str, question_id: str) -> dict[str, Any]:
        """作者不要这几样（或没取到的照现有的接着做）：照现有的回答，记「数据不全」。"""
        q, test, request = await self._open(owner_subject, question_id)
        state = dict(q.data_state or {})
        if state.get("status") not in ("proposed", "missing", None):
            raise ConcurrencyConflictError(
                "Nothing is waiting for your decision", code="test_data_state"
            )
        pins = list(state.get("pins") or []) if state.get("status") == "missing" else []
        gone = list(state.get("missing") or []) or [
            f"{n.get('name') or n.get('doc_type')}：你没要" for n in q.requests
        ]
        state["incomplete"] = list(state.get("incomplete") or []) + gone
        await self._answer(q, test, request, state, Plan(pins=pins))
        return {"status": "answered", "pinned": len(pins)}

    # ---------- 刷新时 ----------
    async def advance(self, test: TestRecord, request: RequestRecord) -> None:
        """在取的：查需求单；办完了重解析，都到了就签、回答，有没到的停下问作者。"""
        if self.supply is None:
            return
        for q in await self._data_questions(test):
            state = dict(q.data_state or {})
            if state.get("status") != "supplying":
                continue
            demand = await self.supply.get(str(state["demand_id"]))
            if str(demand.get("status")) in OPEN_DEMAND:
                continue
            latest = await self.knowbase.refresh(
                request.owner_subject, str(state["resolution_id"])
            )
            done = plan(
                latest,
                _needs(state.get("needs") or q.requests),
                picks=state.get("picks") or {},
                dropped=state.get("dropped") or [],
                after_supply=True,
            )
            if done.missing:
                state.update(
                    {
                        "status": "missing",
                        "missing": done.missing,
                        "pins": done.pins,
                        "incomplete": [
                            i for i in done.incomplete if i not in done.missing
                        ],
                    }
                )
                async with self.repo.transaction():
                    await self.repo.update_question(q.id, data_state=state)
                continue
            await self._answer(q, test, request, state, done)

    async def on_demand_status(self, demand_id: str, owner: str) -> int:
        """knowfeed 推来需求状态变了（契约 knowfeed-supply-status/v1）：等这张单的那次
        测试马上去查一次，办完了就重解析、签、回答（同刷新时的 advance）。回动了几次
        测试。只认这个归属用户的。"""
        done = 0
        for test in await self.repo.list_tests(statuses=ACTIVE_TESTS):
            waiting = [
                q
                for q in await self._data_questions(test)
                if (q.data_state or {}).get("status") == "supplying"
                and str((q.data_state or {}).get("demand_id")) == demand_id
            ]
            if not waiting:
                continue
            request = await self.repo.get_request(test.request_id)
            if request is None or request.owner_subject != owner:
                continue
            await self.advance(test, request)
            done += 1
        return done

    async def finish(self, test: TestRecord) -> None:
        """测试结束：签的授权撤掉；作者确认过的按用例记成测试集的新一版。"""
        answered = [
            q
            for q in await self.repo.list_questions(draft_id=test.draft_id)
            if q.run_id == test.run_id and q.kind == "data" and q.answer is not None
        ]
        grants = [str((q.data_state or {}).get("grant_id")) for q in answered]
        for grant_id in [g for g in grants if g and g != "None"]:
            try:
                await self.knowbase.revoke_case_grant(grant_id)
            except AppException:
                pass  # 到期自己失效
        if not answered:
            return
        async with self.repo.transaction():
            await self.repo.lock_request(test.request_id)
            latest = await self.repo.latest_test_set(test.request_id)
            cases = dict(latest.cases) if latest else {}
            fresh: dict[str, list[dict[str, Any]]] = {}
            for q in answered:
                items = list(((q.answer or {}).get("data") or {}).get("items") or [])
                fresh.setdefault(q.case_id, [])
                for item in items:
                    if item not in fresh[q.case_id]:
                        fresh[q.case_id].append(item)
            for case_id, items in fresh.items():
                kept = list((cases.get(case_id) or {}).get("items") or [])
                for item in items:
                    if item not in kept:
                        kept.append(item)
                cases[case_id] = {"items": kept}
            if latest is not None and cases == latest.cases:
                return
            await self.repo.add_test_set(
                TestSetRecord(
                    id=uuid.uuid4(),
                    request_id=test.request_id,
                    version=(latest.version + 1) if latest else 1,
                    cases=cases,
                    created_at=self.now(),
                    from_test_id=test.id,
                )
            )

    # ---------- 发起时 ----------
    async def with_test_set(
        self,
        request: RequestRecord,
        cases: list[dict[str, Any]],
        *,
        run_id: str,
        ttl_seconds: int,
    ) -> tuple[list[dict[str, Any]], int | None, list[str]]:
        """带测试集发起：测试集里有的用例，每条签一张测试运行授权、带上钉住的几份（不跑
        第 0 步）；没有的用例照常跑第 0 步。回（用例，测试集第几版，签的授权）。"""
        latest = await self.repo.latest_test_set(request.id)
        if latest is None:
            return cases, None, []
        out, grants = [], []
        for case in cases:
            items = list(
                (latest.cases.get(str(case.get("case_id"))) or {}).get("items") or []
            )
            if not items:
                out.append(case)
                continue
            grant_id = await self.knowbase.case_grant(
                owner=request.owner_subject,
                ref=f"{run_id}:{case.get('case_id')}"[:255],
                items=[
                    {
                        k: item[k]
                        for k in (
                            "material_id",
                            "version_no",
                            "sha256",
                            "rendition_kind",
                            "rendition_no",
                            "rendition_sha256",
                            "basis",
                            "basis_ref",
                        )
                        if item.get(k) is not None
                    }
                    for item in items
                ],
                ttl_seconds=ttl_seconds,
            )
            grants.append(grant_id)
            out.append({**case, "data": case_data(items, grant_id)})
        return out, latest.version, grants

    async def revoke(self, grants: list[str]) -> None:
        for grant_id in grants:
            try:
                await self.knowbase.revoke_case_grant(grant_id)
            except AppException:
                pass  # 到期自己失效

    # ---------- 内部 ----------
    async def _mine(
        self, owner_subject: str, test_id: str
    ) -> tuple[TestRecord, RequestRecord]:
        try:
            found = await self.repo.get_test(uuid.UUID(str(test_id)))
        except ValueError:
            found = None
        request = await self.repo.get_request(found.request_id) if found else None
        if found is None or request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such test", code="test_not_found")
        return found, request

    async def _open(
        self, owner_subject: str, question_id: str
    ) -> tuple[QuestionRecord, TestRecord, RequestRecord]:
        try:
            q = await self.repo.get_question(uuid.UUID(str(question_id)))
        except ValueError:
            q = None
        test = await self.repo.find_test_by_run(q.run_id) if q else None
        if q is None or test is None or q.kind != "data":
            raise NotFoundError("No such question", code="question_not_found")
        _, request = await self._mine(owner_subject, str(test.id))
        if test.status not in ACTIVE_TESTS or q.answer is not None:
            raise ConcurrencyConflictError(
                "That test is no longer waiting for this", code="question_stale"
            )
        return q, test, request

    async def _data_questions(self, test: TestRecord) -> list[QuestionRecord]:
        if test.status not in ACTIVE_TESTS:
            return []
        return [
            q
            for q in await self.repo.list_questions(draft_id=test.draft_id)
            if q.run_id == test.run_id and q.kind == "data" and q.answer is None
        ]

    async def _resolve(
        self,
        q: QuestionRecord,
        test: TestRecord,
        request: RequestRecord,
        user_token: UserToken,
    ) -> dict[str, Any]:
        needs = _needs(q.requests)
        made = await self.knowbase.resolve(
            await user_token(),
            consumer=f"knowlab-test:{request.id}",
            needs=[n.model_dump(mode="json") for n in needs],
            task_ref=_task_ref(test, q),
        )
        state = {
            "status": "proposed",
            "resolution_id": str(made["resolution_id"]),
            "revision": int(made["revision"]),
            "needs": [n.model_dump(mode="json") for n in needs],
        }
        async with self.repo.transaction():
            await self.repo.update_question(q.id, data_state=state)
        return state

    async def _answer(
        self,
        q: QuestionRecord,
        test: TestRecord,
        request: RequestRecord,
        state: dict[str, Any],
        draft: Plan,
    ) -> None:
        grant_id = None
        if draft.pins:
            grant_id = await self.knowbase.case_grant(
                owner=request.owner_subject,
                # Same scope as with_test_set and the runtime's test_case_ref.
                # The question id routes the answer, not the data authorization.
                ref=_task_ref(test, q),
                items=draft.grant_items,
                ttl_seconds=grant_ttl(test, self.max_wait_seconds),
            )
        answer = {"data": case_data(draft.pins, grant_id)}
        state.update(
            {
                "status": "answered",
                "grant_id": grant_id,
                "pins": draft.pins,
                "incomplete": list(state.get("incomplete") or []) + draft.incomplete,
            }
        )
        async with self.repo.transaction():
            await self.repo.update_question(
                q.id,
                answer=answer,
                answered_by="user",
                answered_at=self.now(),
                data_state=state,
            )
        await self.deliverer.deliver(q.run_id, request, [q.id])


def _task_ref(test: TestRecord, q: QuestionRecord) -> str:
    return f"{test.run_id}:{q.case_id}"[:255]


def _needs(raw: list[dict[str, Any]]) -> list[DataRequest]:
    try:
        return [DataRequest.model_validate({"formats": ALL_FORMATS, **n}) for n in raw]
    except ValidationError:
        raise _bad("the proposed data is not in the vocabulary") from None


def _revised(given: list[dict[str, Any]], case_id: str) -> list[DataRequest]:
    """作者改过的整份：自己加的没有 key，这里按用例补上；一样一个 key。"""
    if not isinstance(given, list) or not 1 <= len(given) <= MAX_ITEMS:
        raise _bad(f"keep 1 to {MAX_ITEMS} items")
    out = []
    for n, raw in enumerate(given, start=1):
        if not isinstance(raw, dict):
            raise _bad("each item is an object")
        body = {"formats": ALL_FORMATS, **raw}
        body.setdefault("key", f"u{n}")
        body.setdefault("name", str(body.get("doc_type") or "")[:80])
        try:
            out.append(DataRequest.model_validate(body))
        except ValidationError:
            raise _bad("an item is not in the vocabulary") from None
    if len({d.key for d in out}) != len(out):
        raise _bad("each item needs its own key")
    return out
