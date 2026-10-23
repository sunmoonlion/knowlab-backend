"""数据需求声明与测试绑定（SDD 0042 第五节）。

孵化写声明，测试按声明绑数据，交 knowmart 只带声明。
"""

from __future__ import annotations

import pytest

from app.application.errors.exceptions import BadRequestError
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_experts import KnowbotExpertService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.data_needs import (
    bound_problems,
    for_knowmart,
    format_of,
    parse_needs,
)
from app.domain.knowbots.drafts import draft_manifest_problems
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryDataStore,
    MemoryKnowlab,
    accepted_request,
    list_expert,
    manifest,
    result,
)

PDF = b"%PDF-1.7\n..."
REPORTS = {
    "key": "annual_reports",
    "name": "年报",
    "formats": ["pdf"],
    "min_count": 2,
    "max_count": 3,
}
STATEMENTS = {
    "key": "statements",
    "name": "三张报表",
    "formats": ["excel", "csv"],
    "required": False,
    "tables": [{"sheet": "利润表", "columns": ["营业收入"]}],
}


def _bind(dataset_id: str, need: str | None = None) -> dict:
    out = {"dataset_id": dataset_id, "version": "1", "sha256": "a" * 64}
    return out | ({"need": need} if need else {})


def test_needs_follow_knowmarts_rules() -> None:
    needs, problems = parse_needs([REPORTS, STATEMENTS])
    assert problems == [] and [n.key for n in needs] == ["annual_reports", "statements"]
    assert parse_needs(None) == ([], [])
    for bad in (
        [REPORTS, REPORTS],
        [{**REPORTS, "max_count": 1}],
        [{**REPORTS, "formats": ["word"]}],
        [{**REPORTS, "tables": [{"columns": ["x"]}]}],
        [{**REPORTS, "key": f"k{i}", "max_count": 64} for i in range(2)],
        "年报",
    ):
        assert parse_needs(bad)[1], bad


def test_a_draft_with_bad_needs_is_refused_early() -> None:
    doc = manifest(data_needs=[{**REPORTS, "min_count": 0}])
    problems = draft_manifest_problems(doc, runtime="knowbench", version="1")
    assert any("data_needs[0]" in p for p in problems)


def test_bindings_are_checked_against_the_needs() -> None:
    needs, _ = parse_needs([REPORTS, STATEMENTS])
    formats = {"p1": "pdf", "p2": "pdf", "p3": "pdf", "x1": "excel", "t1": "text"}
    ok = [_bind("p1", "annual_reports"), _bind("p2", "annual_reports")]
    assert bound_problems(needs, ok, formats) == []
    assert bound_problems(needs, [*ok, _bind("x1", "statements")], formats) == []
    assert bound_problems(needs, ok[:1], formats) == [
        "annual_reports takes 2 to 3 items, 1 are bound"
    ]
    assert bound_problems(needs, [*ok, _bind("t1", "statements")], formats) == [
        "a data binding for statements is not in a format it takes"
    ]
    assert bound_problems(needs, [*ok, _bind("x1")], formats) == [
        "each data binding must name a declared need"
    ]
    # 旧格式：没有声明，绑定上不许写 need
    assert bound_problems([], [_bind("p1")], formats) == []
    assert bound_problems([], [_bind("p1", "annual_reports")], formats)


def test_formats_follow_the_content_type() -> None:
    assert format_of("application/pdf") == "pdf"
    assert format_of("text/csv; charset=utf-8") == "csv"
    assert format_of("text/markdown; charset=utf-8") == "text"
    assert format_of("image/png") is None


def test_knowmart_gets_the_needs_without_the_test_bindings() -> None:
    new = manifest(data_needs=[REPORTS], data_bindings=[_bind("p1", "annual_reports")])
    assert "data_bindings" not in for_knowmart(new)
    assert for_knowmart(new)["data_needs"] == [REPORTS]
    old = manifest(data_bindings=[_bind("p1")])
    assert for_knowmart(old) is old  # 旧格式原样，摘要不变


@pytest.mark.asyncio
async def test_a_new_format_draft_is_tested_with_its_bindings_and_delivered_without():
    repo = MemoryKnowlab()
    data = KnowbotDataService(repo, MemoryDataStore())
    runtime, knowmart = FakeRuntime(), FakeKnowmart()
    svc = KnowbotCreationService(repo, runtime)
    experts = KnowbotExpertService(repo, knowmart)
    request = await accepted_request(svc)
    first = await data.upload(OWNER, "2023 年报.pdf", PDF)
    second = await data.upload(OWNER, "2024 年报.pdf", PDF + b"2")
    table = await data.upload(OWNER, "说明.txt", "口径".encode())
    bind = lambda d: {  # noqa: E731
        "dataset_id": str(d.id),
        "version": d.version,
        "sha256": d.sha256,
        "need": "annual_reports",
    }
    with pytest.raises(BadRequestError) as refused:  # 文本不是年报要的格式
        await svc.add_draft(
            str(request.id),
            manifest(data_needs=[REPORTS], data_bindings=[bind(first), bind(table)]),
        )
    assert "not in a format it takes" in str(refused.value)
    doc = manifest(data_needs=[REPORTS], data_bindings=[bind(first), bind(second)])
    draft = await svc.add_draft(str(request.id), doc)
    await svc.review_rules(str(draft.id), "admin-1")
    await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    _, sent = runtime.started[0]
    assert sent.manifest["data_bindings"] == doc["data_bindings"]  # 测试带绑定
    runtime.results[sent.run_id] = result(sent.run_id, "passed")
    test = await repo.find_test_by_run(sent.run_id)
    assert test is not None
    await svc.refresh(str(test.id))
    [expert] = repo.experts.values()
    await list_expert(experts, expert.id)
    delivered = knowmart.delivered[0]["manifest"]
    assert "data_bindings" not in delivered and delivered["data_needs"] == [REPORTS]
