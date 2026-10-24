"""创意到专家的测试共用：合格的申请与清单、内存仓储、假运行方、假 knowmart。"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.application.errors.exceptions import (
    BadGatewayError,
    ConcurrencyConflictError,
    ServiceUnavailableError,
)
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
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.lifecycle import (
    ACTIVE_TESTS,
    AcceptanceIn,
    ExpertDecisionIn,
    ExpertReviewIn,
)
from app.domain.knowbots.test_run import (
    CONTRACT,
    AnswersV1,
    CaseOutputsV1,
    RunRequestV1,
    RunResultV1,
)

OWNER = "user-123"  # 与 tests/test_auth_routes_security.session 的用户一致


def request_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "财务分析与预测",
        "runtime": "knowbench",
        "idea": {
            "kind": "six_questions",
            "answers": {
                "solves": "按我的口径分析一家公司的三张表",
                "not_solves": "不给买卖建议",
                "inputs": "我上传的年报",
                "steps": "先算毛利率，毛利率按营业收入减营业成本",
                "outputs": "一页表格",
                "correctness": "毛利率与我手算的一致",
            },
        },
        "cases": [
            {
                "case_id": "c1",
                "input": {"question": "分析 2025 年报"},
                "expectations": [
                    {
                        "artifact": "summary",
                        "path": "gross_margin",
                        "kind": "approx",
                        "value": 0.52,
                        "tolerance": 0.01,
                    }
                ],
            }
        ],
        "consent_test_data": True,
    }
    body.update(overrides)
    return body


def manifest(version: str = "1", **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "knowbot-manifest/v1",
        "name": "财务分析与预测",
        "tagline": "按我的方法分析一家公司的财报",
        "version": version,
        "runtime": "knowbench",
        "pack": {
            "pack_id": "fin-forecast",
            "version": version,
            "workflow": [{"step_id": "s1", "title": "读财报"}],
        },
        "data_bindings": [],
        "rule_sources": [
            {"rule": "毛利率口径", "source": "user_text", "ref": "想法原文第 4 问"}
        ],
    }
    doc.update(overrides)
    return doc


def result(run_id: str, status: str = "passed", **extra: Any) -> RunResultV1:
    cases = extra.pop("cases", None)
    if cases is None:
        case_status = "passed" if status in {"passed", "running"} else "failed"
        cases = [
            {
                "case_id": "c1",
                "status": case_status,
                "steps": [{"step_id": "s1", "accepted": case_status == "passed"}],
                "expectations": [case_status == "passed"],
            }
        ]
        if status in {"accepted", "running"}:
            cases = []
    return RunResultV1.model_validate(
        {"contract": CONTRACT, "run_id": run_id, "status": status, "cases": cases}
        | extra
    )


def all_clear(**over: Any) -> AcceptanceIn:
    """三样都过的受理单。"""
    body: dict[str, Any] = {
        "checks": {c: {"ok": True} for c in ("C-L5", "C-L2", "C-L1")},
        "accepted": True,
    }
    return AcceptanceIn.model_validate(body | over)


async def accepted_request(svc: Any, owner: str = OWNER, **body: Any) -> RequestRecord:
    """申请并受理了 v1 的创意（孵化要从受理了的版本起）。"""
    request = await svc.submit(
        owner, KnowbotRequestIn.model_validate(request_body(**body))
    )
    await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    return request


async def list_expert(
    experts: Any, expert_id: Any, listing: str = "self", owner: str = OWNER
) -> ExpertRecord:
    """交回 → 用户要了 → 运营审过：上架。"""
    await experts.return_to_user(str(expert_id), "admin-1")
    await experts.decide(owner, str(expert_id), ExpertDecisionIn(accept=True))
    return await experts.review(
        str(expert_id), ExpertReviewIn(approved=True, listing=listing), "admin-1"
    )


class MemoryKnowlab:
    def __init__(self) -> None:
        self.requests: dict[uuid.UUID, RequestRecord] = {}
        self.versions: dict[tuple[uuid.UUID, int], RequestVersionRecord] = {}
        self.acceptances: dict[tuple[uuid.UUID, int], AcceptanceRecord] = {}
        self.tests: dict[uuid.UUID, TestRecord] = {}
        self.experts: dict[uuid.UUID, ExpertRecord] = {}
        self.drafts: dict[uuid.UUID, DraftRecord] = {}
        self.datasets: dict[uuid.UUID, DatasetRecord] = {}
        self.questions: dict[uuid.UUID, QuestionRecord] = {}
        self.amendments: dict[uuid.UUID, AmendmentRecord] = {}
        self.outputs: dict[tuple[uuid.UUID, str, str], TestOutputRecord] = {}
        self.consents: dict[str, dict[str, str]] = {}
        self.withdrawals: dict[str, str] = {}  # 待撤的旧同意 → 所有者
        self.commits = 0
        self.rollbacks = 0
        self._tick = 0

    def _now(self) -> datetime:
        self._tick += 1
        return datetime(2026, 10, 16, tzinfo=UTC).replace(microsecond=self._tick)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        snapshot = (
            dict(self.requests),
            dict(self.drafts),
            dict(self.datasets),
            dict(self.questions),
            dict(self.amendments),
            dict(self.outputs),
            dict(self.versions),
            dict(self.acceptances),
            dict(self.tests),
            dict(self.experts),
        )
        try:
            yield
            self.commits += 1
        except BaseException:
            (
                self.requests,
                self.drafts,
                self.datasets,
                self.questions,
                self.amendments,
                self.outputs,
                self.versions,
                self.acceptances,
                self.tests,
                self.experts,
            ) = snapshot
            self.rollbacks += 1
            raise

    async def create_request(self, **fields: Any) -> RequestRecord:
        record = RequestRecord(
            id=uuid.uuid4(), created_at=self._now(), **copy.deepcopy(fields)
        )
        self.requests[record.id] = record
        self.versions[(record.id, 1)] = RequestVersionRecord(
            request_id=record.id,
            version=1,
            idea=copy.deepcopy(record.idea),
            cases=copy.deepcopy(record.cases),
            consent_version=record.consent_version,
            created_by=record.owner_subject,
            created_at=record.created_at,
        )
        return record

    # ---------- 创意版本与受理单 ----------
    async def add_version(self, **fields: Any) -> RequestVersionRecord:
        record = RequestVersionRecord(created_at=self._now(), **copy.deepcopy(fields))
        key = (record.request_id, record.version)
        assert key not in self.versions
        self.versions[key] = record
        self.requests[record.request_id] = replace(
            self.requests[record.request_id],
            latest_version=record.version,
            idea=copy.deepcopy(record.idea),
            cases=copy.deepcopy(record.cases),
            consent_version=record.consent_version,
        )
        return record

    async def get_version(
        self, request_id: uuid.UUID, version: int
    ) -> RequestVersionRecord | None:
        return self.versions.get((request_id, version))

    async def list_versions(
        self, request_ids: Any = None
    ) -> Sequence[RequestVersionRecord]:
        return sorted(
            (
                v
                for v in self.versions.values()
                if request_ids is None or v.request_id in request_ids
            ),
            key=lambda v: (str(v.request_id), v.version),
        )

    async def add_acceptance(self, record: AcceptanceRecord) -> None:
        key = (record.request_id, record.version)
        if key in self.acceptances:
            raise ConcurrencyConflictError("decided", code="already_decided")
        self.acceptances[key] = record

    async def get_acceptance(
        self, request_id: uuid.UUID, version: int
    ) -> AcceptanceRecord | None:
        return self.acceptances.get((request_id, version))

    async def list_acceptances(
        self, request_ids: Any = None
    ) -> Sequence[AcceptanceRecord]:
        return sorted(
            (
                a
                for a in self.acceptances.values()
                if request_ids is None or a.request_id in request_ids
            ),
            key=lambda a: (str(a.request_id), a.version),
        )

    async def get_request(self, request_id: uuid.UUID) -> RequestRecord | None:
        return self.requests.get(request_id)

    async def list_requests_for_owner(
        self, owner_subject: str
    ) -> Sequence[RequestRecord]:
        mine = [r for r in self.requests.values() if r.owner_subject == owner_subject]
        return sorted(mine, key=lambda r: r.created_at or 0, reverse=True)

    async def list_requests(self) -> Sequence[RequestRecord]:
        return sorted(
            self.requests.values(), key=lambda r: r.created_at or 0, reverse=True
        )

    async def lock_request(self, request_id: uuid.UUID) -> None:
        return None

    async def create_draft(self, **fields: Any) -> DraftRecord:
        record = DraftRecord(
            id=uuid.uuid4(),
            status="pending_test",
            created_at=self._now(),
            **copy.deepcopy(fields),
        )
        self.drafts[record.id] = record
        return record

    async def get_draft(
        self, draft_id: uuid.UUID, *, for_update: bool = False
    ) -> DraftRecord | None:
        return self.drafts.get(draft_id)

    async def list_drafts(
        self,
        *,
        status: str | None = None,
        request_id: uuid.UUID | None = None,
        request_ids: Any = None,
    ) -> Sequence[DraftRecord]:
        found = [
            d
            for d in self.drafts.values()
            if (status is None or d.status == status)
            and (request_id is None or d.request_id == request_id)
            and (request_ids is None or d.request_id in request_ids)
        ]
        return sorted(found, key=lambda d: d.created_at or 0)

    async def update_draft(self, draft_id: uuid.UUID, **changes: Any) -> None:
        self.drafts[draft_id] = replace(self.drafts[draft_id], **changes)

    async def set_case_answers(
        self, request_id: uuid.UUID, answers: dict[str, Any]
    ) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], case_answers=copy.deepcopy(answers)
        )

    # ---------- 测试 ----------
    async def create_test(self, record: TestRecord) -> TestRecord:
        if record.status in ACTIVE_TESTS and any(
            t.request_id == record.request_id and t.status in ACTIVE_TESTS
            for t in self.tests.values()
        ):
            raise ConcurrencyConflictError("running", code="test_running")
        self.tests[record.id] = record
        return record

    async def get_test(
        self, test_id: uuid.UUID, *, for_update: bool = False
    ) -> TestRecord | None:
        return self.tests.get(test_id)

    async def list_tests(
        self, *, request_ids: Any = None, draft_id: uuid.UUID | None = None
    ) -> Sequence[TestRecord]:
        return sorted(
            (
                t
                for t in self.tests.values()
                if (request_ids is None or t.request_id in request_ids)
                and (draft_id is None or t.draft_id == draft_id)
            ),
            key=lambda t: (str(t.request_id), t.number),
        )

    async def update_test(self, test_id: uuid.UUID, **changes: Any) -> None:
        self.tests[test_id] = replace(self.tests[test_id], **changes)

    async def find_test_by_run(self, run_id: str) -> TestRecord | None:
        return next((t for t in self.tests.values() if t.run_id == run_id), None)

    # ---------- 专家 ----------
    async def create_expert(self, record: ExpertRecord) -> ExpertRecord:
        if any(
            e.request_id == record.request_id and e.version == record.version
            for e in self.experts.values()
        ):
            raise ConcurrencyConflictError("taken", code="expert_version_taken")
        self.experts[record.id] = record
        return record

    async def get_expert(
        self, expert_id: uuid.UUID, *, for_update: bool = False
    ) -> ExpertRecord | None:
        return self.experts.get(expert_id)

    async def list_experts(self, request_ids: Any = None) -> Sequence[ExpertRecord]:
        return sorted(
            (
                e
                for e in self.experts.values()
                if request_ids is None or e.request_id in request_ids
            ),
            key=lambda e: (str(e.request_id), e.version),
        )

    async def update_expert(self, expert_id: uuid.UUID, **changes: Any) -> None:
        self.experts[expert_id] = replace(self.experts[expert_id], **changes)

    async def find_listed_expert(
        self, knowbot_id: str, version: str
    ) -> ExpertRecord | None:
        for e in self.experts.values():
            if (
                e.knowmart_knowbot_id == knowbot_id
                and str(e.version) == version
                and e.listed_at is not None
                and e.stopped_at is None
            ):
                return e
        return None

    # ---------- 补充方法 ----------
    async def create_amendment(
        self, *, request_id: uuid.UUID, owner_subject: str, text: str
    ) -> AmendmentRecord:
        record = AmendmentRecord(
            id=uuid.uuid4(),
            request_id=request_id,
            owner_subject=owner_subject,
            text=text,
            created_at=self._now(),
        )
        self.amendments[record.id] = record
        return record

    async def list_amendments(self, request_id: uuid.UUID) -> Sequence[AmendmentRecord]:
        found = [a for a in self.amendments.values() if a.request_id == request_id]
        return sorted(found, key=lambda a: a.created_at or 0)

    async def get_amendment(
        self, amendment_id: uuid.UUID, *, for_update: bool = False
    ) -> AmendmentRecord | None:
        return self.amendments.get(amendment_id)

    async def decline_amendment(
        self, amendment_id: uuid.UUID, *, reason: str, by: str, at: Any
    ) -> None:
        self.amendments[amendment_id] = replace(
            self.amendments[amendment_id],
            declined_reason=reason,
            declined_by=by,
            declined_at=at,
        )

    # ---------- 测试中的问题 ----------
    async def add_questions(
        self, draft_id: uuid.UUID, run_id: str, questions: list[dict[str, Any]]
    ) -> None:
        seen = {(q.draft_id, q.run_id, q.question_id) for q in self.questions.values()}
        for q in questions:
            if (draft_id, run_id, q["question_id"]) in seen:
                continue
            record = QuestionRecord(
                id=uuid.uuid4(),
                draft_id=draft_id,
                run_id=run_id,
                question_id=q["question_id"],
                case_id=q["case_id"],
                kind=q["kind"],
                text=q["text"],
                options=list(q.get("options") or []),
                allow_free_text=bool(q.get("allow_free_text", True)),
                asked_at=q["asked_at"],
                step_id=q.get("step_id"),
                key=q.get("key"),
                route=q.get("route"),
            )
            self.questions[record.id] = record

    async def get_question(
        self, question: uuid.UUID, *, for_update: bool = False
    ) -> QuestionRecord | None:
        return self.questions.get(question)

    async def list_questions(
        self, *, draft_id: uuid.UUID | None = None, open_only: bool = False
    ) -> Sequence[QuestionRecord]:
        return [
            q
            for q in self.questions.values()
            if (draft_id is None or q.draft_id == draft_id)
            and (not open_only or q.answer is None)
        ]

    async def update_question(self, question: uuid.UUID, **changes: Any) -> None:
        self.questions[question] = replace(self.questions[question], **changes)

    # ---------- 用户的数据 ----------
    async def lock_owner_data(self, owner_subject: str) -> None:
        return None

    async def create_dataset(self, record: DatasetRecord) -> DatasetRecord:
        stored = replace(record, created_at=self._now())
        self.datasets[record.id] = stored
        return stored

    async def list_datasets(self, owner_subject: str) -> Sequence[DatasetRecord]:
        mine = [
            d
            for d in self.datasets.values()
            if d.owner_subject == owner_subject and d.deleted_at is None
        ]
        return sorted(mine, key=lambda d: d.created_at or 0, reverse=True)

    async def get_dataset(self, dataset_id: uuid.UUID) -> DatasetRecord | None:
        return self.datasets.get(dataset_id)

    async def get_dataset_by_item(self, item_id: str) -> DatasetRecord | None:
        return next((d for d in self.datasets.values() if d.item_id == item_id), None)

    async def mark_dataset_deleted(self, dataset_id: uuid.UUID, at: Any) -> None:
        self.datasets[dataset_id] = replace(self.datasets[dataset_id], deleted_at=at)

    async def set_dataset_item(self, dataset_id: uuid.UUID, item_id: str) -> None:
        self.datasets[dataset_id] = replace(self.datasets[dataset_id], item_id=item_id)

    async def datasets_to_import(self, limit: int = 50) -> Sequence[DatasetRecord]:
        found = [
            d
            for d in self.datasets.values()
            if d.item_id is None and d.object_key and d.deleted_at is None
        ]
        return sorted(found, key=lambda d: d.created_at or 0)[:limit]

    async def get_test_consent(self, owner_subject: str) -> dict[str, str] | None:
        found = self.consents.get(owner_subject)
        return dict(found) if found else None

    async def put_test_consent(
        self, owner_subject: str, consent_id: str, items_digest: str
    ) -> None:
        self.consents[owner_subject] = {
            "consent_id": consent_id,
            "items_digest": items_digest,
        }

    async def delete_test_consent(self, owner_subject: str) -> None:
        self.consents.pop(owner_subject, None)

    async def add_consent_withdrawal(self, owner_subject: str, consent_id: str) -> None:
        self.withdrawals.setdefault(consent_id, owner_subject)

    async def consent_withdrawals(self, owner_subject: str) -> list[str]:
        return [c for c, o in self.withdrawals.items() if o == owner_subject]

    async def drop_consent_withdrawal(self, consent_id: str) -> None:
        self.withdrawals.pop(consent_id, None)

    async def dataset_in_use(self, owner_subject: str, dataset_id: uuid.UUID) -> bool:
        """同 SQL：待测、测试中的准专家，或还没走完、没停用的专家绑着。"""
        live = {
            e.draft_id
            for e in self.experts.values()
            if e.stopped_at is None
            and e.user_accepted is not False
            and e.review_approved is not False
        }
        for draft in self.drafts.values():
            request = self.requests[draft.request_id]
            if request.owner_subject != owner_subject:
                continue
            if draft.status not in ("pending_test", "testing") and draft.id not in live:
                continue
            bindings = draft.manifest.get("data_bindings") or []
            if any(b.get("dataset_id") == str(dataset_id) for b in bindings):
                return True
        return False

    async def set_consent_version(self, request_id: uuid.UUID, version: int) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], consent_version=version
        )

    async def test_output_cases(self, draft_id: uuid.UUID, run_id: str) -> set[str]:
        return {c for (d, r, c) in self.outputs if (d, r) == (draft_id, run_id)}

    async def expire_test_outputs(self, before: datetime) -> int:
        """同 SQL：上架早于 before，或有更新的一稿建于 before 之前。"""
        from app.domain.knowbots.drafts import OUTPUTS_EXPIRED

        count = 0
        for key, out in list(self.outputs.items()):
            draft = self.drafts[out.draft_id]
            gone = any(
                e.draft_id == draft.id and e.listed_at and e.listed_at < before
                for e in self.experts.values()
            )
            newer = any(
                d.request_id == draft.request_id
                and d.created_at
                and draft.created_at
                and draft.created_at < d.created_at < before
                for d in self.drafts.values()
            )
            if out.outputs is not None and (gone or newer):
                self.outputs[key] = replace(out, outputs=None, note=OUTPUTS_EXPIRED)
                count += 1
        return count

    async def put_test_output(self, record: TestOutputRecord) -> None:
        key = (record.draft_id, record.run_id, record.case_id)
        self.outputs.setdefault(key, replace(record, fetched_at=self._now()))

    async def list_test_outputs(
        self, draft_id: uuid.UUID, run_id: str
    ) -> Sequence[TestOutputRecord]:
        return sorted(
            (
                o
                for (d, r, _), o in self.outputs.items()
                if (d, r) == (draft_id, run_id)
            ),
            key=lambda o: o.case_id,
        )


class FakeRuntime:
    """按 run_id 记下请求；start 回 accepted，结果由测试塞进 results。"""

    def __init__(self, names: frozenset[str] = frozenset({"knowbench"})) -> None:
        self.names = names
        self.started: list[tuple[str, RunRequestV1]] = []
        self.results: dict[str, RunResultV1] = {}
        self.fail_start = False
        self.answers: list[tuple[str, AnswersV1]] = []
        self.fail_answer = False
        # 一条用例的产出（SDD 0037）：(run_id, case_id) → 产出；
        # 没有的回 None（运行方 404）
        self.case_outputs: dict[tuple[str, str], CaseOutputsV1] = {}
        self.outputs_asked: list[tuple[str, str]] = []
        self.fail_outputs = False
        self.bad_outputs = False  # 回包不合契约

    def registered(self) -> frozenset[str]:
        return self.names

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1:
        if self.fail_start:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        self.started.append((runtime, request))
        return self.results.get(request.run_id, result(request.run_id, "accepted"))

    async def status(self, runtime: str, run_id: str) -> RunResultV1:
        return self.results.get(run_id, result(run_id, "running"))

    async def outputs(
        self, runtime: str, run_id: str, case_id: str
    ) -> CaseOutputsV1 | None:
        self.outputs_asked.append((run_id, case_id))
        if self.fail_outputs:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        if self.bad_outputs:
            raise BadGatewayError("bad", code="test_run_contract_invalid")
        return self.case_outputs.get((run_id, case_id))

    async def answer(
        self, runtime: str, run_id: str, answers: AnswersV1
    ) -> RunResultV1:
        if self.fail_answer:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        self.answers.append((run_id, answers))
        return self.results.get(run_id, result(run_id, "running"))


class FakeKnowmart:
    def __init__(self) -> None:
        self.delivered: list[dict[str, Any]] = []
        self.stopped: list[dict[str, Any]] = []
        self.unlisted: list[dict[str, Any]] = []
        self.fail = False

    async def deliver(self, **body: Any) -> dict[str, Any]:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.delivered.append(body)
        found = {
            "knowbot_id": "kb-1",
            "version": body["manifest"]["version"],
            "digest": "sha256:" + "d" * 64,
        }
        if body.get("request_public"):
            found["listing_status"] = "pending"
        return found

    async def stop(self, **body: Any) -> None:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.stopped.append(body)

    async def set_unlisted(self, **body: Any) -> None:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.unlisted.append(body)


class MemoryDataStore:
    """假的对象存储：记下放进去的与删掉的。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def put(
        self, *, object_key: str, data: bytes, content_type: str
    ) -> tuple[str, str]:
        import hashlib

        self.objects[object_key] = data
        return f"v-{len(self.objects)}", hashlib.sha256(data).hexdigest()

    async def delete(self, *, object_key: str) -> None:
        self.deleted.append(object_key)

    async def get(
        self, *, object_key: str, version_id: str, sha256: str, max_bytes: int
    ) -> bytes:
        import hashlib

        data = self.objects[object_key]
        if hashlib.sha256(data).hexdigest() != sha256 or len(data) > max_bytes:
            raise RuntimeError("stored_object_digest_mismatch")
        return data


def waiting(run_id: str) -> RunResultV1:
    """跑到要人决定的地方：一个审批、一个开步前提问、一个 Codex 反问。"""
    asked = "2026-10-17T00:00:00Z"
    return RunResultV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": run_id,
            "status": "waiting",
            "waited_seconds": 600,
            "questions": [
                {
                    "question_id": "q-approve",
                    "case_id": "c1",
                    "step_id": "s1",
                    "kind": "tool_approval",
                    "text": "执行命令 python calc.py？",
                    "asked_at": asked,
                },
                {
                    "question_id": "q-scope",
                    "case_id": "c1",
                    "step_id": "s1",
                    "kind": "ask_before",
                    "key": "period",
                    "text": "看哪几年？",
                    "options": [{"id": "3y", "label": "近三年"}],
                    "asked_at": asked,
                },
                {
                    "question_id": "q-judge",
                    "case_id": "c1",
                    "kind": "question",
                    "text": "毛利率按哪个口径？",
                    "asked_at": asked,
                },
            ],
        }
    )
