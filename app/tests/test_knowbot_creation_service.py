"""分身创建的流程：提交 → 建草稿 → 核出处 → 测试 → 通过即交付；未通过、超时、失败。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    manifest,
    request_body,
    result,
)


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 16, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def parts():
    repo, runtime, knowmart, clock = (
        MemoryKnowlab(),
        FakeRuntime(),
        FakeKnowmart(),
        Clock(),
    )
    return (
        KnowbotCreationService(repo, runtime, knowmart, now=clock),
        repo,
        runtime,
        knowmart,
        clock,
    )


async def _reviewed_draft(svc: KnowbotCreationService):
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    return request, draft


@pytest.mark.asyncio
async def test_happy_path_tests_then_delivers_exactly_the_tested_manifest(parts):
    svc, repo, runtime, knowmart, _ = parts
    request, draft = await _reviewed_draft(svc)
    started = await svc.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600
    )
    assert started.status == "accepted"
    sent_runtime, sent = runtime.started[0]
    assert sent_runtime == "knowbench"
    assert sent.owner_subject == OWNER
    assert sent.manifest == manifest()
    assert [c.case_id for c in sent.cases] == ["c1"]
    assert repo.drafts[draft.id].status == "testing"

    still = await svc.refresh(str(draft.id))
    assert still.status == "testing"

    runtime.results[sent.run_id] = result(sent.run_id, "passed")
    done = await svc.refresh(str(draft.id))
    assert done.status == "delivered"
    assert done.report and done.report["status"] == "passed"
    assert done.delivery == {
        "knowbot_id": "kb-1",
        "version": "1",
        "delivered_at": "2026-10-16T12:00:00+00:00",  # 专家栏要写交付时间（SDD 0037）
    }
    assert knowmart.delivered == [
        {
            "delivery_id": f"knowlab-draft-{draft.id}",
            "owner_subject": OWNER,
            "knowbot_ref": f"knowlab-{request.id}",
            "manifest": manifest(),
        }
    ]
    # 交付过后，下一份草稿是第 2 版
    assert (await svc.add_draft(str(request.id), manifest("2"))).version == "2"


@pytest.mark.asyncio
async def test_rules_must_be_reviewed_before_testing(parts):
    svc, *_ = parts
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    draft = await svc.add_draft(str(request.id), manifest())
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert raised.value.code == "rules_not_reviewed"


@pytest.mark.asyncio
async def test_a_failed_run_is_reported_and_a_new_draft_keeps_the_version(parts):
    svc, repo, runtime, knowmart, _ = parts
    request, draft = await _reviewed_draft(svc)
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[run.run_id] = result(run.run_id, "failed")
    failed = await svc.refresh(str(draft.id))
    assert failed.status == "failed" and failed.report["status"] == "failed"
    assert knowmart.delivered == []
    with pytest.raises(ConcurrencyConflictError):
        await svc.deliver(str(draft.id))
    again = await svc.add_draft(str(request.id), manifest())
    assert (again.version, again.revision) == ("1", 2)
    assert again.rules_reviewed_by is None  # 新草稿要重新核出处
    mine = await svc.my_requests(OWNER)
    assert [d.status for d in mine[0][1]] == ["failed", "pending_test"]


@pytest.mark.asyncio
async def test_only_one_draft_in_progress_per_request(parts):
    svc, *_ = parts
    request, _ = await _reviewed_draft(svc)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.add_draft(str(request.id), manifest())
    assert raised.value.code == "draft_in_progress"


@pytest.mark.asyncio
async def test_bad_manifests_and_unknown_runtimes_are_refused(parts):
    svc, *_ = parts
    with pytest.raises(BadRequestError) as raised:
        await svc.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body(runtime="nowhere"))
        )
    assert raised.value.code == "runtime_not_registered"
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    with pytest.raises(BadRequestError) as raised:
        await svc.add_draft(str(request.id), manifest(version="2"))
    assert raised.value.code == "manifest_invalid"
    with pytest.raises(NotFoundError) as missing:
        await svc.add_draft("not-a-uuid", manifest())
    assert missing.value.code == "request_not_found"
    with pytest.raises(NotFoundError) as missing:
        await svc.refresh("00000000-0000-4000-8000-000000000000")
    assert missing.value.code == "draft_not_found"


@pytest.mark.asyncio
async def test_runtime_down_returns_the_draft_to_pending(parts):
    svc, repo, runtime, *_ = parts
    _, draft = await _reviewed_draft(svc)
    runtime.fail_start = True
    with pytest.raises(AppException):
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    back = repo.drafts[draft.id]
    assert back.status == "pending_test"
    assert back.last_error == "start failed: knowbench_unavailable"
    assert back.rules_reviewed_by == "admin-1"
    runtime.fail_start = False
    retried = await svc.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600
    )
    assert retried.run_id.endswith("-2")  # 每次发起换一个 run_id


@pytest.mark.asyncio
async def test_delivery_failure_is_recorded_and_can_be_retried(parts):
    svc, repo, runtime, knowmart, _ = parts
    _, draft = await _reviewed_draft(svc)
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[run.run_id] = result(run.run_id, "passed")
    knowmart.fail = True
    passed = await svc.refresh(str(draft.id))
    assert passed.status == "passed"
    assert passed.last_error == "delivery failed: knowmart_unavailable"
    knowmart.fail = False
    delivered = await svc.deliver(str(draft.id))
    assert delivered.status == "delivered" and delivered.last_error is None


@pytest.mark.asyncio
async def test_stuck_tests_can_be_returned_only_after_their_time(parts):
    svc, repo, runtime, _, clock = parts
    _, draft = await _reviewed_draft(svc)
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    clock.at += timedelta(seconds=600)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.reset_stuck(str(draft.id))
    assert raised.value.code == "not_stuck"
    clock.at += timedelta(minutes=6)
    await svc.reset_stuck(str(draft.id))
    assert repo.drafts[draft.id].status == "pending_test"
    # 旧那次测试的结果晚到，不算数
    runtime.results[run.run_id] = result(run.run_id, "passed")
    stale = await svc.refresh(str(draft.id))
    assert stale.status == "pending_test"


@pytest.mark.asyncio
async def test_users_only_see_their_own_requests(parts):
    svc, *_ = parts
    await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    await svc.submit("someone-else", KnowbotRequestIn.model_validate(request_body()))
    assert len(await svc.my_requests(OWNER)) == 1
    assert await svc.my_requests("") == []
    assert len(await svc.all_requests()) == 2
