"""测试里专家提出的数据（SDD 0046 第三节，decisions ⑭）。

第 0 步提出的报成 data 问题 → 作者打开测试页时在 app:knowlab 的范围里解析 →
作者确认（齐的签这条用例的测试授权、回答；缺的确认一次、交 knowfeed）→ 刷新时
查需求单，取齐了重解析、签、回答；没取到的停下问作者 → 测试结束撤授权、记成
测试集 → 重测带测试集，不跑第 0 步。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.knowbot_questions import KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_test_data import KnowbotTestData
from app.domain.knowbots.data_plan import item_ref
from app.domain.knowbots.test_run import CONTRACT, RunResultV1
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryKnowlab,
    accepted_request,
    manifest,
    own_computer,
    platform_draft,
    result,
)

SHA = {"2025": "a" * 64, "2024": "b" * 64}
PROPOSED = [
    {
        "key": "req1_1",
        "name": "最近一年的年报",
        "subject": "600519.SH",
        "doc_type": "annual_report",
        "period": {"kind": "fixed", "start": "2025-01-01", "end": "2025-12-31"},
        "formats": ["pdf"],
        "reading": "pages",
        "required": True,
    },
    {
        "key": "req1_2",
        "name": "上一年比较",
        "subject": "600519.SH",
        "doc_type": "annual_report",
        "period": {"kind": "fixed", "start": "2024-01-01", "end": "2024-12-31"},
        "formats": ["pdf"],
        "reading": "pages",
    },
]


def candidate(year: str) -> dict[str, Any]:
    return {
        "material_id": f"m-{year}",
        "version_no": 1,
        "sha256": SHA[year],
        "basis": "public",
        "rendition_kind": "pages",
        "rendition_no": 1,
        "rendition_sha256": "c" * 64,
        "name": f"贵州茅台{year}年年度报告.pdf",
        "media_type": "application/pdf",
        "size_bytes": 100,
    }


class FakeKnowbase:
    """2025 年报已有、2024 年报要去取（arrives 决定补采后到没到）。"""

    def __init__(self) -> None:
        self.needs: list[dict[str, Any]] = []
        self.revision = 0
        self.arrives = True
        self.fetched = False
        self.calls: list[tuple[str, Any]] = []

    def _latest(self) -> dict[str, Any]:
        items = []
        for need in self.needs:
            year = str(need["period"]["start"])[:4]
            here = year == "2025" or (self.fetched and self.arrives)
            items.append(
                {
                    "need_key": need["key"],
                    "key": {
                        "subject": need.get("subject"),
                        "doc_type": "annual_report",
                        "period_start": f"{year}-01-01",
                        "period_end": f"{year}-12-31",
                    },
                    "status": "resolved" if here else "to_confirm",
                    "chosen": [candidate(year)] if here and year in SHA else [],
                    "candidates": [],
                    "reason": None,
                }
            )
        return {"resolution_id": "res-1", "revision": self.revision, "items": items}

    async def resolve(self, user_token, *, consumer, needs, task_ref):
        self.calls.append(("resolve", (consumer, task_ref)))
        self.needs, self.revision = list(needs), 1
        return self._latest()

    async def resolution(self, user_token, resolution_id):
        return self._latest()

    async def revise_needs(self, user_token, resolution_id, needs):
        self.calls.append(("revise", needs))
        self.needs, self.revision = list(needs), self.revision + 1
        return self._latest()

    async def confirm(self, user_token, *, items, task_ref, resolution_id, revision):
        self.calls.append(("confirm", items))
        return {
            "confirmation_id": "conf-1",
            "items": [
                {"item_id": f"c{n}", "key": i["key"], "reading": "pages"}
                for n, i in enumerate(items, start=1)
            ],
            "branches": [
                {"item_id": f"c{n}", "branch": "supply"}
                for n in range(1, len(items) + 1)
            ],
        }

    async def refresh(self, subject, resolution_id):
        self.fetched, self.revision = True, self.revision + 1
        return self._latest()

    async def case_grant(self, *, owner, ref, items, ttl_seconds):
        self.calls.append(("grant", (owner, ref, copy.deepcopy(items))))
        return f"g-{sum(1 for c in self.calls if c[0] == 'grant')}"

    async def revoke_case_grant(self, grant_id):
        self.calls.append(("revoke", grant_id))


class FakeSupply:
    def __init__(self) -> None:
        self.status = "fetching"
        self.submitted: list[dict[str, Any]] = []

    def configured(self) -> bool:
        return True

    async def submit(self, **kw: Any) -> dict[str, Any]:
        self.submitted.append(kw)
        return {"demand_id": "d-1", "status": "fetching"}

    async def get(self, demand_id: str) -> dict[str, Any]:
        return {"demand_id": demand_id, "status": self.status}


def asking(run_id: str) -> RunResultV1:
    return RunResultV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": run_id,
            "status": "waiting",
            "questions": [
                {
                    "question_id": "data:t1:r1",
                    "case_id": "c1",
                    "kind": "data",
                    "text": "专家要这些数据：最近一年的年报、上一年比较",
                    "allow_free_text": False,
                    "asked_at": "2026-10-28T00:00:00Z",
                    "requests": PROPOSED,
                }
            ],
        }
    )


async def token() -> str:
    return "author-token"


@pytest.fixture
async def world():
    repo, runtime = MemoryKnowlab(), FakeRuntime()
    own_computer(repo)  # 平台测试在作者的电脑上跑（decisions ⑯）
    kb, supply = FakeKnowbase(), FakeSupply()
    asks = KnowbotQuestionService(repo, runtime)
    data = KnowbotTestData(
        repo, kb, supply, asks, supply_enabled=True, max_wait_seconds=600
    )
    svc = KnowbotCreationService(repo, runtime, on_refresh=asks.resend, test_data=data)
    request = await accepted_request(svc)
    draft = await platform_draft(svc, str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    runtime.results[test.run_id] = asking(test.run_id)
    await svc.refresh(str(test.id))
    return svc, data, repo, runtime, kb, supply, draft, test


async def test_the_author_confirms_what_step_zero_proposed(world) -> None:
    svc, data, repo, runtime, kb, supply, draft, test = world
    [question] = repo.questions.values()
    assert (question.kind, question.route) == ("data", "user")
    assert [n["key"] for n in question.requests] == ["req1_1", "req1_2"]
    with pytest.raises(NotFoundError):
        await data.pending("someone-else", str(test.id), token)
    [shown] = await data.pending(OWNER, str(test.id), token)
    assert kb.calls[0] == (
        "resolve",
        (f"knowlab-test:{draft.request_id}", f"{test.run_id}:c1"),
    )
    assert shown["status"] == "proposed"
    assert [i["status"] for i in shown["items"]] == ["resolved", "to_confirm"]
    assert shown["items"][0]["ref"] == item_ref(shown["items"][0])
    # 换选的不在候选里：确认不了
    with pytest.raises(BadRequestError):
        await data.confirm(
            OWNER,
            shown["question_id"],
            token,
            picks={shown["items"][0]["ref"]: {"material_id": "m-x", "version_no": 1}},
        )
    done = await data.confirm(OWNER, shown["question_id"], token)
    assert done["status"] == "supplying"
    assert supply.submitted[0]["confirmation_id"] == "conf-1"
    assert runtime.answers == []
    # 还在取：刷新不动
    await svc.refresh(str(test.id))
    assert runtime.answers == []
    # 取齐了：重解析、签这条用例的授权、回答运行方
    supply.status = "ready"
    await svc.refresh(str(test.id))
    [(run_id, body)] = runtime.answers
    [answer] = body.answers
    assert (run_id, answer.question_id, answer.answered_by) == (
        test.run_id,
        "data:t1:r1",
        "user",
    )
    assert answer.data is not None and answer.data.grant_id == "g-1"
    assert [i.material_id for i in answer.data.items] == ["m-2025", "m-2024"]
    [grant] = [c for c in kb.calls if c[0] == "grant"]
    assert grant[1][0] == OWNER
    assert {i["basis"] for i in grant[1][2]} == {"public"}
    # 测试结束：撤授权，作者确认过的记成测试集第 1 版
    runtime.results[test.run_id] = result(test.run_id, "passed")
    await svc.refresh(str(test.id))
    assert ("revoke", "g-1") in kb.calls
    [made] = repo.test_sets
    assert made.version == 1 and made.from_test_id == test.id
    assert [i["material_id"] for i in made.cases["c1"]["items"]] == ["m-2025", "m-2024"]


async def test_a_retest_carries_the_test_set_and_skips_step_zero(world) -> None:
    svc, data, repo, runtime, kb, supply, draft, test = world
    [shown] = await data.pending(OWNER, str(test.id), token)
    kb.fetched = True  # 都有了：当场签、回答
    await data.confirm(OWNER, shown["question_id"], token)
    runtime.results[test.run_id] = result(test.run_id, "error")
    await svc.refresh(str(test.id))
    assert repo.test_sets[0].version == 1
    again = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    sent = runtime.started[-1][1]
    case = sent.cases[0]
    assert case.data is not None and case.data.grant_id is not None
    assert [i.material_id for i in case.data.items] == ["m-2025", "m-2024"]
    found = repo.tests[again.id]
    assert found.test_set_version == 1 and found.case_grants == [case.data.grant_id]


async def test_the_author_changes_drops_or_declines(world) -> None:
    svc, data, repo, runtime, kb, supply, draft, test = world
    [shown] = await data.pending(OWNER, str(test.id), token)
    [first, _] = shown["needs"]
    # 只留第一样、换成另一家：整份交 knowbase 重解析
    done = await data.confirm(
        OWNER,
        shown["question_id"],
        token,
        needs=[{**first, "subject": "000858.SZ"}],
    )
    assert done["status"] == "answered"
    [(_, revised)] = [c for c in kb.calls if c[0] == "revise"]
    assert [(n["key"], n["subject"]) for n in revised] == [("req1_1", "000858.SZ")]
    [(_, body)] = runtime.answers
    assert [i.material_id for i in body.answers[0].data.items] == ["m-2025"]


async def test_what_did_not_arrive_waits_for_the_author(world) -> None:
    svc, data, repo, runtime, kb, supply, draft, test = world
    kb.arrives = False
    [shown] = await data.pending(OWNER, str(test.id), token)
    await data.confirm(OWNER, shown["question_id"], token)
    supply.status = "ready"
    await svc.refresh(str(test.id))
    [waiting] = await data.pending(OWNER, str(test.id), token)
    assert waiting["status"] == "missing"
    assert waiting["missing"] == ["上一年比较 600519.SH 2024-01-01–2024-12-31：没取到"]
    assert runtime.answers == []
    # 照现有的接着做：带已到的那一份回答
    await data.decline(OWNER, shown["question_id"])
    [(_, body)] = runtime.answers
    assert [i.material_id for i in body.answers[0].data.items] == ["m-2025"]
    assert await data.pending(OWNER, str(test.id), token) == []


async def test_declining_everything_answers_with_no_data(world) -> None:
    svc, data, repo, runtime, kb, supply, draft, test = world
    [shown] = await data.pending(OWNER, str(test.id), token)
    await data.decline(OWNER, shown["question_id"])
    [(_, body)] = runtime.answers
    assert body.answers[0].data is not None and body.answers[0].data.items == ()
    assert [c for c in kb.calls if c[0] == "grant"] == []
    [question] = repo.questions.values()
    assert question.data_state["incomplete"] == [
        "最近一年的年报：你没要",
        "上一年比较：你没要",
    ]


async def test_the_author_tests_on_his_own_computer() -> None:
    """自助测试（SDD 0046 第三节）：选他接入的电脑与根目录、他设费用上限；测试记模式与
    电脑，下次默认用这台。只有自己的准专家。"""
    repo, runtime = MemoryKnowlab(), FakeRuntime()
    svc = KnowbotCreationService(repo, runtime)
    request = await accepted_request(svc)
    draft = await platform_draft(svc, str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    env = "11111111-1111-4111-8111-111111111111"
    first = await svc.my_computers(OWNER, str(draft.id))
    assert first["last"] is None and first["computers"][0]["environment_id"] == env
    with pytest.raises(NotFoundError):
        await svc.start_self_test(
            "someone-else",
            str(draft.id),
            environment_id=env,
            workspace_root="/home/me/work",
            budget_limit="5",
            timeout_seconds=600,
        )
    test = await svc.start_self_test(
        OWNER,
        str(draft.id),
        environment_id=env,
        workspace_root="/home/me/work",
        budget_limit="5",
        timeout_seconds=600,
    )
    sent = runtime.started[-1][1]
    assert sent.runner is not None and sent.runner.kind == "user"
    assert (sent.runner.environment_id, sent.runner.workspace_root) == (
        env,
        "/home/me/work",
    )
    assert sent.budget.limit == "5"
    found = repo.tests[test.id]
    assert (found.mode, found.environment_id, found.started_by) == ("self", env, OWNER)
    again = await svc.my_computers(OWNER, str(draft.id))
    assert again["last"] == {"environment_id": env, "workspace_root": "/home/me/work"}
    with pytest.raises(NotFoundError):
        await svc.refresh_mine("someone-else", str(test.id))
    assert (await svc.refresh_mine(OWNER, str(test.id))).id == test.id
