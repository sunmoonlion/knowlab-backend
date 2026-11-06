"""知识库栏与工作稿里挑数据（SDD 0046 第六节；专家孵化方案第 7.1、8.2 节）：栏里照
knowbase 给的列、凭作者本人令牌；挑的只能是栏里的，挑的时候记下格式（描述、就绪的
读法），不取内容；AI 一轮把记下的格式交给运行方，平台人员发起的那一轮也一样；挑数据
不走存格子。内存仓储、假 knowbase、假运行方。"""

from __future__ import annotations

from typing import Any

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_library import KnowbotLibraryService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from tests.knowbot_fakes import (
    MATERIAL,
    OWNER,
    FakeIncubator,
    FakeLibrary,
    FakeRuntime,
    MemoryKnowlab,
    all_clear,
    own_computer,
    request_body,
    shelf_entry,
)

TOKEN = "token-of-the-author"


async def token() -> str:
    return TOKEN


class Wired:
    def __init__(self) -> None:
        self.repo = MemoryKnowlab()
        self.runtime = FakeRuntime()
        self.incubator = FakeIncubator()
        self.knowbase = FakeLibrary(
            [shelf_entry(), shelf_entry("m-notes", "我的笔记.md", doc_type=None)]
        )
        self.svc = KnowbotCreationService(self.repo, self.runtime)
        self.library = KnowbotLibraryService(self.repo, self.knowbase)
        self.works = KnowbotWorkdraftService(self.repo)
        self.inc = KnowbotIncubationService(self.repo, self.incubator, self.runtime)

    async def request(self, owner: str = OWNER, **body: Any):
        return await self.svc.submit(
            owner,
            KnowbotRequestIn.model_validate(
                request_body(mode="self", budget_limit=None, **body)
            ),
        )


@pytest.fixture
def w() -> Wired:
    wired = Wired()
    own_computer(wired.repo)
    return wired


async def test_the_column_lists_what_knowbase_shows_to_knowlab(w: Wired) -> None:
    found = await w.library.shown(token)
    assert w.knowbase.tokens == [TOKEN]  # 凭作者本人的令牌现取
    assert [m["material_id"] for m in found] == [MATERIAL, "m-notes"]
    first = found[0]
    assert first["name"] == "2024 年报.pdf"
    assert first["description"]["subject"] == "600519.SH"
    assert first["description"]["status"] == "confirmed"
    assert (first["version_no"], first["media_type"], first["size_bytes"]) == (
        1,
        "application/pdf",
        2048,
    )


async def test_picking_records_the_format_not_the_content(w: Wired) -> None:
    request = await w.request()
    stored = await w.library.pick(
        OWNER, str(request.id), revision=0, picks=[MATERIAL], user_token=token
    )
    assert stored.body["data_picks"] == [MATERIAL]
    assert stored.revision == 1
    fmt = stored.data_formats[MATERIAL]
    assert fmt["name"] == "2024 年报.pdf"
    assert fmt["media_type"] == "application/pdf"
    assert fmt["description"]["doc_type"] == "annual_report"
    assert fmt["description"]["period_end"] == "2024-12-31"
    # 只记就绪的读法；没成的不算
    assert fmt["readings"] == ["pages", "text"]
    assert fmt["tables"] == []
    # 挑的编号不是作者的判断文字：不记出处
    assert stored.sources == {}


async def test_only_materials_in_the_column_can_be_picked(w: Wired) -> None:
    request = await w.request()
    with pytest.raises(BadRequestError) as refused:
        await w.library.pick(
            OWNER, str(request.id), revision=0, picks=["m-other"], user_token=token
        )
    assert refused.value.code == "pick_not_shown"
    with pytest.raises(BadRequestError) as twice:
        await w.library.pick(
            OWNER,
            str(request.id),
            revision=0,
            picks=[MATERIAL, MATERIAL],
            user_token=token,
        )
    assert twice.value.code == "picks_invalid"


async def test_picking_none_clears_and_a_stale_revision_conflicts(w: Wired) -> None:
    request = await w.request()
    await w.library.pick(
        OWNER, str(request.id), revision=0, picks=[MATERIAL], user_token=token
    )
    with pytest.raises(ConcurrencyConflictError) as stale:
        await w.library.pick(
            OWNER, str(request.id), revision=0, picks=[], user_token=token
        )
    assert stale.value.code == "workdraft_revision"
    asked = len(w.knowbase.tokens)
    cleared = await w.library.pick(
        OWNER, str(request.id), revision=1, picks=[], user_token=token
    )
    assert cleared.body["data_picks"] == [] and cleared.data_formats == {}
    assert len(w.knowbase.tokens) == asked  # 都不要了就不去问 knowbase


async def test_someone_elses_name_is_not_found(w: Wired) -> None:
    request = await w.request()
    with pytest.raises(NotFoundError):
        await w.library.pick(
            "user-other", str(request.id), revision=0, picks=[], user_token=token
        )


async def test_data_picks_are_not_saved_as_a_cell(w: Wired) -> None:
    request = await w.request()
    with pytest.raises(BadRequestError) as refused:
        await w.works.save(
            OWNER, str(request.id), revision=0, edits=[("data_picks", [MATERIAL])]
        )
    assert refused.value.code == "workdraft_path_invalid"


async def test_a_round_hands_over_the_recorded_formats(w: Wired) -> None:
    request = await w.request()
    await w.library.pick(
        OWNER,
        str(request.id),
        revision=0,
        picks=["m-notes", MATERIAL],
        user_token=token,
    )
    await w.inc.start(OWNER, str(request.id), action="draft")
    (sent,) = w.incubator.started
    assert [f.name for f in sent.data_formats] == ["我的笔记.md", "2024 年报.pdf"]
    assert sent.data_formats[1].readings == ("pages", "text")
    assert sent.data_formats[0].description.doc_type is None
    dumped = sent.model_dump(mode="json")["data_formats"][1]
    assert set(dumped) == {"name", "media_type", "description", "readings", "tables"}


async def test_the_platform_round_uses_the_same_formats(w: Wired) -> None:
    """平台人员发起的那一轮没有作者的令牌：交的是作者挑的时候记下的那份。"""
    request = await w.svc.submit(
        OWNER,
        KnowbotRequestIn.model_validate(request_body()),  # 请平台孵化
    )
    await w.library.pick(
        OWNER, str(request.id), revision=0, picks=[MATERIAL], user_token=token
    )
    await w.svc.accept(str(request.id), 1, all_clear(), "admin-1")
    asked = len(w.knowbase.tokens)
    await w.inc.operator_start(str(request.id), action="draft")
    (sent,) = w.incubator.started
    assert [f.name for f in sent.data_formats] == ["2024 年报.pdf"]
    assert len(w.knowbase.tokens) == asked


async def test_real_axes_reach_incubation_and_do_not_change_with_library(w: Wired):
    w.knowbase.tables[MATERIAL] = [
        {"name": "报表", "columns": ["项目", "2024"], "rows": ["收入"]}
    ]
    request = await w.request()
    picked = await w.library.pick(
        OWNER, str(request.id), revision=0, picks=[MATERIAL], user_token=token
    )
    assert picked.data_formats[MATERIAL]["tables"] == w.knowbase.tables[MATERIAL]
    assert picked.data_formats[MATERIAL]["description"]["language"] == "zh"
    w.knowbase.tables[MATERIAL] = []
    await w.inc.start(OWNER, str(request.id), action="draft")
    (sent,) = w.incubator.started
    assert sent.data_formats[0].tables[0].rows == ("收入",)


async def test_wrong_format_version_does_not_save_partial_picks(w: Wired):
    from app.application.errors.exceptions import BadGatewayError

    request = await w.request()
    original = w.knowbase.read_format

    async def wrong(*args):
        result = await original(*args)
        result["sha256"] = "b" * 64
        return result

    w.knowbase.read_format = wrong
    with pytest.raises(BadGatewayError, match="selected version"):
        await w.library.pick(
            OWNER, str(request.id), revision=0, picks=[MATERIAL], user_token=token
        )
    stored = await w.repo.get_workdraft(request.id)
    assert stored is None  # 失败发生在写事务前，不留下半份工作稿


async def test_unreadable_table_axes_still_save_the_pick(w: Wired):
    # 账 186：knowbase 读不出表轴（回 None），这一份照「表轴未知」存下，别的照旧
    request = await w.request()

    async def unreadable(*args):
        return None

    w.knowbase.read_format = unreadable
    picked = await w.library.pick(
        OWNER, str(request.id), revision=0, picks=[MATERIAL], user_token=token
    )
    assert picked.data_formats[MATERIAL]["tables"] == []
    assert picked.data_formats[MATERIAL]["readings"]
