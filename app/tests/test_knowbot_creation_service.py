"""创意到专家的流程（SDD 0037）：申请 → 受理 → 孵化 → 核出处 → 测试 → 通过即专家。

另有改创意出新版本、不受理、用户审核准专家、没通过、平台出错重测（新编号）、超时。
"""

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
from app.domain.knowbots.lifecycle import RevisionIn, UserReviewIn
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
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
    repo, runtime, clock = MemoryKnowlab(), FakeRuntime(), Clock()
    return KnowbotCreationService(repo, runtime, now=clock), repo, runtime, clock


async def _accepted(svc: KnowbotCreationService, **body):
    request = await svc.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body(**body))
    )
    await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    return request


async def _reviewed_draft(svc: KnowbotCreationService, **body):
    request = await _accepted(svc, **body)
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    return request, draft


def _revision(**answers) -> RevisionIn:
    body = request_body()
    body["idea"]["answers"] |= answers
    return RevisionIn.model_validate(
        {k: body[k] for k in ("idea", "cases", "consent_test_data")}
    )


@pytest.mark.asyncio
async def test_a_pass_makes_the_expert_with_the_drafts_version(parts):
    svc, repo, runtime, _ = parts
    request, draft = await _reviewed_draft(svc)
    test = await svc.start_test(
        str(draft.id), budget_limit="20", timeout_seconds=600, starter="admin-1"
    )
    assert (test.number, test.status, test.started_by) == (1, "queued", "admin-1")
    assert test.run_id == f"knowlab-test-{test.id}"
    sent_runtime, sent = runtime.started[0]
    assert sent_runtime == "knowbench"
    assert sent.owner_subject == OWNER
    assert sent.manifest == manifest()
    assert [c.case_id for c in sent.cases] == ["c1"]
    assert repo.drafts[draft.id].status == "testing"

    still = await svc.refresh(str(test.id))
    assert still.status == "running"

    runtime.results[test.run_id] = result(test.run_id, "passed")
    passed = await svc.refresh(str(test.id))
    assert passed.status == "passed" and passed.finished_at is not None
    assert passed.report and passed.report["status"] == "passed"
    assert repo.drafts[draft.id].status == "passed"
    [expert] = repo.experts.values()
    assert (expert.version, expert.draft_id, expert.test_id) == (1, draft.id, test.id)
    assert expert.returned_at is None  # 交回由管理端发起
    # 出过第 1 版专家，下一位准专家冲第 2 版
    nxt = await svc.add_draft(str(request.id), manifest("2"))
    assert (nxt.version, nxt.revision) == ("2", 2)


@pytest.mark.asyncio
async def test_incubation_needs_an_accepted_version(parts):
    svc, *_ = parts
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.add_draft(str(request.id), manifest())
    assert raised.value.code == "version_not_accepted"
    rejected = all_clear(
        checks={
            "C-L5": {"ok": False, "note": "要给买卖建议"},
            "C-L2": {"ok": True},
            "C-L1": {"ok": True},
        },
        accepted=False,
        reason="投资建议类不做（C-L5）",
    )
    found = await svc.accept(str(request.id), 1, rejected, "admin-1")
    assert found.accepted is False and found.reason == "投资建议类不做（C-L5）"
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    assert raised.value.code == "already_decided"  # 一个版本一张，写了不改
    with pytest.raises(ConcurrencyConflictError):
        await svc.add_draft(str(request.id), manifest())
    # 用户改创意出 v2，v2 受理了才能孵化
    v2 = await svc.revise(OWNER, str(request.id), _revision(not_solves="不给任何建议"))
    assert v2.version == 2
    await svc.accept(str(request.id), 2, all_clear(), "admin-1")
    draft = await svc.add_draft(str(request.id), manifest())
    assert draft.request_version == 2
    with pytest.raises(ConcurrencyConflictError):  # 选不受理的版本不行
        await svc.add_draft(str(request.id), manifest(), request_version=1)
    with pytest.raises(NotFoundError):
        await svc.accept(str(request.id), 9, all_clear(), "admin-1")


@pytest.mark.asyncio
async def test_revising_adds_a_version_and_keeps_the_old_one(parts):
    svc, repo, *_ = parts
    request = await _accepted(svc)
    await svc.revise(OWNER, str(request.id), _revision(steps="先算毛利率，再算净利率"))
    assert repo.requests[request.id].latest_version == 2
    v1, v2 = repo.versions[(request.id, 1)], repo.versions[(request.id, 2)]
    assert v1.idea["answers"]["steps"] == "先算毛利率，毛利率按营业收入减营业成本"
    assert v2.idea["answers"]["steps"] == "先算毛利率，再算净利率"
    assert repo.requests[request.id].idea == v2.idea  # 名称指向最新版本
    with pytest.raises(NotFoundError):
        await svc.revise("someone-else", str(request.id), _revision())
    # v2 还没受理：孵化默认选最新受理了的 v1
    draft = await svc.add_draft(str(request.id), manifest())
    assert draft.request_version == 1


@pytest.mark.asyncio
async def test_amendment_citations_are_retired(parts):
    svc, *_ = parts
    request = await _accepted(svc)
    cited = manifest(
        rule_sources=[
            {
                "rule": "口径",
                "source": "user_text",
                "ref": "amendment:00000000-0000-4000-8000-000000000001",
            }
        ]
    )
    with pytest.raises(BadRequestError) as raised:
        await svc.add_draft(str(request.id), cited)
    assert "amendments are retired" in str(raised.value)


@pytest.mark.asyncio
async def test_the_user_reviews_a_draft_before_it_is_tested(parts):
    svc, *_ = parts
    request, draft = await _reviewed_draft(svc)
    await svc.request_user_review(str(draft.id), "admin-1")
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.request_user_review(str(draft.id), "admin-1")
    assert raised.value.code == "review_requested"
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert raised.value.code == "review_pending"
    with pytest.raises(NotFoundError):
        await svc.answer_user_review(
            "someone-else", str(draft.id), UserReviewIn(ok=True)
        )
    with pytest.raises(ValueError):
        UserReviewIn(ok=False)  # 不行要说哪里不对
    answered = await svc.answer_user_review(
        OWNER, str(draft.id), UserReviewIn(ok=False, note="第二步口径不对")
    )
    assert answered.user_review and answered.user_review["ok"] is False
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert raised.value.code == "review_rejected"
    # 再孵化：新准专家，版本号不变、修订号加一
    again = await svc.add_draft(str(request.id), manifest())
    assert (again.version, again.revision) == ("1", 2)
    await svc.request_user_review(str(again.id), "admin-1")
    await svc.answer_user_review(OWNER, str(again.id), UserReviewIn(ok=True))
    await svc.review_rules(str(again.id), "admin-1")
    test = await svc.start_test(str(again.id), budget_limit="20", timeout_seconds=600)
    assert test.number == 1


@pytest.mark.asyncio
async def test_rules_must_be_reviewed_before_testing(parts):
    svc, *_ = parts
    request = await _accepted(svc)
    draft = await svc.add_draft(str(request.id), manifest())
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert raised.value.code == "rules_not_reviewed"


@pytest.mark.asyncio
async def test_a_failed_test_ends_the_draft_and_a_new_draft_follows(parts):
    svc, repo, runtime, _ = parts
    request, draft = await _reviewed_draft(svc)
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[test.run_id] = result(test.run_id, "failed")
    failed = await svc.refresh(str(test.id))
    assert failed.status == "failed" and failed.report["status"] == "failed"
    assert repo.drafts[draft.id].status == "failed"
    assert repo.experts == {}
    with pytest.raises(ConcurrencyConflictError):  # 没通过的不再测
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    again = await svc.add_draft(str(request.id), manifest())
    assert (again.version, again.revision) == ("1", 2)
    assert again.rules_reviewed_by is None  # 新准专家要重新核出处


@pytest.mark.asyncio
async def test_only_one_test_runs_per_knowbot(parts):
    svc, *_ = parts
    request, draft = await _reviewed_draft(svc)
    other = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(other.id), "admin-1")
    await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.start_test(str(other.id), budget_limit="20", timeout_seconds=600)
    assert raised.value.code == "test_running"


@pytest.mark.asyncio
async def test_a_draft_whose_version_is_taken_is_not_tested(parts):
    svc, _, runtime, _ = parts
    request, draft = await _reviewed_draft(svc)
    spare = await svc.add_draft(str(request.id), manifest())  # 也冲第 1 版
    await svc.review_rules(str(spare.id), "admin-1")
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[test.run_id] = result(test.run_id, "passed")
    await svc.refresh(str(test.id))
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.start_test(str(spare.id), budget_limit="20", timeout_seconds=600)
    assert raised.value.code == "version_taken"


@pytest.mark.asyncio
async def test_bad_manifests_and_unknown_runtimes_are_refused(parts):
    svc, *_ = parts
    with pytest.raises(BadRequestError) as raised:
        await svc.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body(runtime="nowhere"))
        )
    assert raised.value.code == "runtime_not_registered"
    request = await _accepted(svc)
    with pytest.raises(BadRequestError) as raised:
        await svc.add_draft(str(request.id), manifest(version="2"))
    assert raised.value.code == "manifest_invalid"
    with pytest.raises(NotFoundError) as missing:
        await svc.add_draft("not-a-uuid", manifest())
    assert missing.value.code == "request_not_found"
    with pytest.raises(NotFoundError) as missing:
        await svc.refresh("00000000-0000-4000-8000-000000000000")
    assert missing.value.code == "test_not_found"


@pytest.mark.asyncio
async def test_a_runtime_error_is_a_platform_error_and_the_retest_is_a_new_number(
    parts,
):
    svc, repo, runtime, _ = parts
    _, draft = await _reviewed_draft(svc)
    runtime.fail_start = True
    with pytest.raises(AppException):
        await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    [first] = repo.tests.values()
    assert first.status == "platform_error"
    assert first.error == "start failed: knowbench_unavailable"
    back = repo.drafts[draft.id]
    assert back.status == "pending_test" and back.rules_reviewed_by == "admin-1"
    runtime.fail_start = False
    second = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert second.number == 2 and second.run_id != first.run_id
    runtime.results[second.run_id] = result(second.run_id, "error")
    ended = await svc.refresh(str(second.id))
    assert ended.status == "platform_error" and ended.error == "runtime: error"
    assert repo.drafts[draft.id].status == "pending_test"
    third = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert third.number == 3
    # 结果定了不改：前两次照旧在
    assert [t.status for t in sorted(repo.tests.values(), key=lambda t: t.number)] == [
        "platform_error",
        "platform_error",
        "queued",
    ]


@pytest.mark.asyncio
async def test_stuck_tests_end_as_platform_errors_only_after_their_time(parts):
    svc, repo, runtime, clock = parts
    _, draft = await _reviewed_draft(svc)
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    clock.at += timedelta(seconds=600)
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.reset_stuck(str(test.id))
    assert raised.value.code == "not_stuck"
    clock.at += timedelta(minutes=6)
    await svc.reset_stuck(str(test.id))
    assert repo.tests[test.id].status == "platform_error"
    assert repo.tests[test.id].error == "timed out"
    assert repo.drafts[draft.id].status == "pending_test"
    # 旧那次测试的结果晚到，不算数
    runtime.results[test.run_id] = result(test.run_id, "passed")
    stale = await svc.refresh(str(test.id))
    assert stale.status == "platform_error"
    assert repo.experts == {}
    with pytest.raises(ConcurrencyConflictError):
        await svc.reset_stuck(str(test.id))


@pytest.mark.asyncio
async def test_users_only_reach_their_own(parts):
    svc, *_ = parts
    request, draft = await _reviewed_draft(svc)
    with pytest.raises(NotFoundError):
        await svc.my_request("someone-else", str(request.id))
    with pytest.raises(NotFoundError):
        await svc.my_draft("someone-else", str(draft.id))
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    with pytest.raises(NotFoundError):
        await svc.my_test("someone-else", str(test.id))
    assert (await svc.my_test(OWNER, str(test.id)))[0].id == test.id
