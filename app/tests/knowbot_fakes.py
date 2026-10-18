"""分身创建测试共用：合格的提交与清单、内存仓储、假运行方、假 knowmart。"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.application.errors.exceptions import ServiceUnavailableError
from app.application.ports.knowbots import (
    DatasetRecord,
    DraftRecord,
    QuestionRecord,
    RequestRecord,
)
from app.domain.knowbots.test_run import (
    CONTRACT,
    AnswersV1,
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


class MemoryKnowlab:
    def __init__(self) -> None:
        self.requests: dict[uuid.UUID, RequestRecord] = {}
        self.drafts: dict[uuid.UUID, DraftRecord] = {}
        self.datasets: dict[uuid.UUID, DatasetRecord] = {}
        self.questions: dict[uuid.UUID, QuestionRecord] = {}
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
        )
        try:
            yield
            self.commits += 1
        except BaseException:
            self.requests, self.drafts, self.datasets, self.questions = snapshot
            self.rollbacks += 1
            raise

    async def create_request(self, **fields: Any) -> RequestRecord:
        record = RequestRecord(
            id=uuid.uuid4(), created_at=self._now(), **copy.deepcopy(fields)
        )
        self.requests[record.id] = record
        return record

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
        self, *, status: str | None = None, request_id: uuid.UUID | None = None
    ) -> Sequence[DraftRecord]:
        found = [
            d
            for d in self.drafts.values()
            if (status is None or d.status == status)
            and (request_id is None or d.request_id == request_id)
        ]
        return sorted(found, key=lambda d: d.created_at or 0)

    async def update_draft(self, draft_id: uuid.UUID, **changes: Any) -> None:
        self.drafts[draft_id] = replace(self.drafts[draft_id], **changes)

    async def update_request_cases(
        self, request_id: uuid.UUID, cases: list[dict[str, Any]]
    ) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], cases=copy.deepcopy(cases)
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

    async def mark_dataset_deleted(self, dataset_id: uuid.UUID, at: Any) -> None:
        self.datasets[dataset_id] = replace(self.datasets[dataset_id], deleted_at=at)

    async def dataset_in_use(self, owner_subject: str, dataset_id: uuid.UUID) -> bool:
        for draft in self.drafts.values():
            request = self.requests[draft.request_id]
            if request.owner_subject != owner_subject or draft.status == "failed":
                continue
            bindings = draft.manifest.get("data_bindings") or []
            if any(b.get("dataset_id") == str(dataset_id) for b in bindings):
                return True
        return False

    async def find_delivered_draft(
        self, knowbot_id: str, version: str
    ) -> DraftRecord | None:
        for draft in sorted(self.drafts.values(), key=lambda d: d.created_at or 0):
            delivery = draft.delivery or {}
            if (
                draft.status == "delivered"
                and delivery.get("knowbot_id") == knowbot_id
                and draft.version == version
            ):
                return draft
        return None

    async def find_testing_draft(self, run_id: str) -> DraftRecord | None:
        for draft in self.drafts.values():
            if draft.status == "testing" and draft.run_id == run_id:
                return draft
        return None


class FakeRuntime:
    """按 run_id 记下请求；start 回 accepted，结果由测试塞进 results。"""

    def __init__(self, names: frozenset[str] = frozenset({"knowbench"})) -> None:
        self.names = names
        self.started: list[tuple[str, RunRequestV1]] = []
        self.results: dict[str, RunResultV1] = {}
        self.fail_start = False
        self.answers: list[tuple[str, AnswersV1]] = []
        self.fail_answer = False

    def registered(self) -> frozenset[str]:
        return self.names

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1:
        if self.fail_start:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        self.started.append((runtime, request))
        return self.results.get(request.run_id, result(request.run_id, "accepted"))

    async def status(self, runtime: str, run_id: str) -> RunResultV1:
        return self.results.get(run_id, result(run_id, "running"))

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
        self.fail = False

    async def deliver(self, **body: Any) -> dict[str, Any]:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.delivered.append(body)
        return {"knowbot_id": "kb-1", "version": body["manifest"]["version"]}


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
