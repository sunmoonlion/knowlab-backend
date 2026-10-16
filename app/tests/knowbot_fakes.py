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
from app.application.ports.knowbots import DraftRecord, RequestRecord
from app.domain.knowbots.test_run import CONTRACT, RunRequestV1, RunResultV1

OWNER = "user-123"  # 与 tests/test_auth_routes_security.session 的用户一致


def request_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "财务分析与预测",
        "runtime": "investment",
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
        "runtime": "investment",
        "pack": {
            "pack_id": "fin-forecast",
            "version": version,
            "workflow": [{"step_id": "s1", "title": "读财报"}],
        },
        "data_bindings": [
            {"dataset_id": "ds-600519", "version": "3", "sha256": "a" * 64}
        ],
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
        self.commits = 0
        self.rollbacks = 0
        self._tick = 0

    def _now(self) -> datetime:
        self._tick += 1
        return datetime(2026, 10, 16, tzinfo=UTC).replace(microsecond=self._tick)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        snapshot = (dict(self.requests), dict(self.drafts))
        try:
            yield
            self.commits += 1
        except BaseException:
            self.requests, self.drafts = snapshot
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


class FakeRuntime:
    """按 run_id 记下请求；start 回 accepted，结果由测试塞进 results。"""

    def __init__(self, names: frozenset[str] = frozenset({"investment"})) -> None:
        self.names = names
        self.started: list[tuple[str, RunRequestV1]] = []
        self.results: dict[str, RunResultV1] = {}
        self.fail_start = False

    def registered(self) -> frozenset[str]:
        return self.names

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1:
        if self.fail_start:
            raise ServiceUnavailableError("down", code="investment_unavailable")
        self.started.append((runtime, request))
        return self.results.get(request.run_id, result(request.run_id, "accepted"))

    async def status(self, runtime: str, run_id: str) -> RunResultV1:
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
