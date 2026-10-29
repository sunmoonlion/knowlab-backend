"""工作稿的服务（专家孵化方案第五、九至十一节）：存格子与编辑记录、revision、出一版、
导入、复制自己的专家、预览。内存仓储。"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import (
    PROGRAM_REVIEWER,
    KnowbotWorkdraftService,
)
from app.domain.knowbots.drafts import KnowbotRequestIn, draft_manifest_problems
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryKnowlab,
    accepted_request,
    manifest,
    platform_draft,
    request_body,
)

STEP = {
    "id": "overview",
    "title": "数据摸底",
    "does": "看清数据有哪些表、哪些报告期。",
    "why": "先知道手上有什么。",
    "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
    "outputs": [{"name": "periods", "type": "list"}],
    "checks": [{"template": "list_min", "params": {"path": "periods"}}],
}
CELLS: list[tuple[str, Any]] = [
    ("scope/does", "A 股非金融公司的财务分析"),
    ("steps/overview", STEP),
]


@pytest.fixture
def wired():
    repo, runtime = MemoryKnowlab(), FakeRuntime()
    return (
        repo,
        KnowbotWorkdraftService(repo),
        KnowbotCreationService(repo, runtime),
        runtime,
    )


async def _request(svc: KnowbotCreationService, owner: str = OWNER):
    return await svc.submit(owner, KnowbotRequestIn.model_validate(request_body()))


@pytest.mark.asyncio
async def test_a_new_name_has_no_workdraft_until_the_first_save(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    _, found = await works.get(OWNER, str(request.id))
    assert found is None
    saved = await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    assert saved.revision == 1
    assert saved.body["steps"][0]["title"] == "数据摸底"
    # 有字的格子每格一条编辑记录，出处引它
    entries = list(repo.entries.values())
    assert {e.path for e in entries} == set(saved.sources)
    assert all(
        (e.kind, e.actor_subject, e.actor_role) == ("edit", OWNER, "author")
        for e in entries
    )
    rule = saved.sources["steps/overview/rules/r1"]
    entry = repo.entries[uuid.UUID(rule["ref"].removeprefix("record:"))]
    assert rule["kind"] == "user_text" and entry.text == "合并、母公司都有时问用户。"


@pytest.mark.asyncio
async def test_only_changed_cells_get_a_new_record(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    first = await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    count = len(repo.entries)
    # 整步重存一次，只改了一条规则：只有那一格记新的，别的出处照旧
    step = STEP | {"rules": [{"id": "r1", "text": "合并、母公司都有时先问用户。"}]}
    second = await works.save(
        OWNER, str(request.id), revision=1, edits=[("steps/overview", step)]
    )
    assert len(repo.entries) == count + 1
    changed = "steps/overview/rules/r1"
    assert second.sources[changed] != first.sources[changed]
    assert {p: s for p, s in second.sources.items() if p != changed} == {
        p: s for p, s in first.sources.items() if p != changed
    }
    # 删掉的格子出处跟着去掉
    third = await works.save(
        OWNER, str(request.id), revision=2, edits=[(changed, None)]
    )
    assert changed not in third.sources


@pytest.mark.asyncio
async def test_a_stale_revision_is_a_conflict_and_nothing_is_kept(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    count = len(repo.entries)
    with pytest.raises(ConcurrencyConflictError) as caught:
        await works.save(OWNER, str(request.id), revision=0, edits=[("name", "新")])
    assert caught.value.code == "workdraft_revision"
    assert len(repo.entries) == count
    with pytest.raises(ConcurrencyConflictError):
        await works.save(OWNER, str(request.id), revision=5, edits=[("name", "新")])


@pytest.mark.asyncio
async def test_bad_cells_are_refused(wired) -> None:
    _, works, svc, _ = wired
    request = await _request(svc)
    with pytest.raises(BadRequestError) as caught:
        await works.save(OWNER, str(request.id), revision=0, edits=[("who", "x")])
    assert caught.value.code == "workdraft_path_invalid"
    with pytest.raises(BadRequestError) as caught:
        await works.save(
            OWNER, str(request.id), revision=0, edits=[("steps/1bad", {"title": "x"})]
        )
    assert caught.value.code == "workdraft_invalid"
    with pytest.raises(BadRequestError):
        await works.save(OWNER, str(request.id), revision=0, edits=[])


@pytest.mark.asyncio
async def test_not_mine_is_not_found(wired) -> None:
    _, works, svc, _ = wired
    theirs = await _request(svc, "someone-else")
    for call in (
        works.get(OWNER, str(theirs.id)),
        works.save(OWNER, str(theirs.id), revision=0, edits=CELLS),
        works.preview(OWNER, str(theirs.id)),
        works.publish(OWNER, str(theirs.id)),
        works.import_manifest(OWNER, str(theirs.id), revision=0, manifest=manifest()),
    ):
        with pytest.raises(NotFoundError):
            await call


@pytest.mark.asyncio
async def test_publish_makes_a_self_draft_and_keeps_the_workdraft(wired) -> None:
    repo, works, svc, runtime = wired
    request = await _request(svc)  # 自助不受理
    saved = await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    draft = await works.publish(OWNER, str(request.id))
    assert (draft.mode, draft.version, draft.revision) == ("self", "1", 1)
    assert draft.request_version == 1 and draft.status == "pending_test"
    assert draft.rules_reviewed_by == PROGRAM_REVIEWER
    assert (
        draft_manifest_problems(draft.manifest, runtime="knowbench", version="1") == []
    )
    assert draft.manifest["pack"]["pack_id"] == f"knowlab-{request.id}"
    refs = {s["ref"] for s in draft.manifest["rule_sources"]}
    assert refs == {
        saved.sources["steps/overview/rules/r1"]["ref"],
        saved.sources["steps/overview/checks/0"]["ref"],
    }
    # 工作稿不变，接着改再出一版：修订号加 1，版本号照旧（还没出专家）
    _, still = await works.get(OWNER, str(request.id))
    assert still == saved
    await works.save(OWNER, str(request.id), revision=1, edits=[("name", "新名字")])
    again = await works.publish(OWNER, str(request.id))
    assert (again.version, again.revision, again.manifest["name"]) == (
        "1",
        2,
        "新名字",
    )
    # 出出来的一版可以自己拿去测（出处由程序核过）
    test = await svc.start_self_test(
        OWNER,
        str(again.id),
        environment_id="0" * 8 + "-0000-4000-8000-" + "0" * 12,
        workspace_root="D:/tests",
        budget_limit="20",
        timeout_seconds=600,
    )
    assert test.mode == "self"
    assert runtime.started[-1][1].manifest == again.manifest


@pytest.mark.asyncio
async def test_publish_refuses_cells_without_a_source_and_names_them(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    saved = await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    sources = dict(saved.sources)
    del sources["steps/overview/does"]
    repo.workdrafts[request.id] = replace(saved, sources=sources)
    with pytest.raises(BadRequestError) as caught:
        await works.publish(OWNER, str(request.id))
    assert caught.value.code == "workdraft_invalid"
    assert "steps/overview/does: this cell has no source" in caught.value.msg
    assert repo.drafts == {}
    # 预览照样回清单，问题按格子列出
    built, problems, version = await works.preview(OWNER, str(request.id))
    assert version == "1" and built["pack"]["workflow"][0]["title"] == "数据摸底"
    assert [(p.path, p.message) for p in problems] == [
        ("steps/overview/does", "this cell has no source")
    ]


@pytest.mark.asyncio
async def test_a_record_of_someone_else_does_not_count(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    saved = await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    ref = saved.sources["steps/overview/rules/r1"]["ref"]
    entry = repo.entries[uuid.UUID(ref.removeprefix("record:"))]
    repo.entries[entry.id] = replace(entry, actor_subject="someone-else")
    _, problems, _ = await works.preview(OWNER, str(request.id))
    assert [(p.path, p.message) for p in problems] == [
        ("steps/overview/rules/r1", "the source record is not the author's")
    ]


@pytest.mark.asyncio
async def test_an_empty_or_unfinished_workdraft_is_not_published(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    with pytest.raises(BadRequestError):
        await works.publish(OWNER, str(request.id))
    _, problems, _ = await works.preview(OWNER, str(request.id))
    assert {p.path for p in problems} >= {"scope/does", "steps"}
    await works.save(
        OWNER, str(request.id), revision=0, edits=[("scope/does", "分析财报")]
    )
    with pytest.raises(BadRequestError) as caught:
        await works.publish(OWNER, str(request.id))
    assert "steps: add at least one step" in caught.value.msg
    assert repo.drafts == {}


@pytest.mark.asyncio
async def test_version_follows_the_experts_already_made(wired) -> None:
    repo, works, svc, _ = wired
    request = await accepted_request(svc)
    old = await platform_draft(svc, str(request.id), manifest())
    from app.application.ports.knowbots import ExpertRecord

    repo.experts[uuid.uuid4()] = ExpertRecord(
        id=uuid.uuid4(),
        request_id=request.id,
        version=1,
        draft_id=old.id,
        created_at=repo._now(),
    )
    await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    draft = await works.publish(OWNER, str(request.id))
    assert (draft.version, draft.revision) == ("2", 2)
    assert old.mode == "platform"


@pytest.mark.asyncio
async def test_import_replaces_the_workdraft_with_one_import_record(wired) -> None:
    repo, works, svc, _ = wired
    request = await _request(svc)
    await works.save(OWNER, str(request.id), revision=0, edits=CELLS)
    first = await works.publish(OWNER, str(request.id))
    other = await _request(svc)
    found, notes = await works.import_manifest(
        OWNER, str(other.id), revision=0, manifest=first.manifest
    )
    assert notes == []
    assert found.revision == 1
    (entry,) = [e for e in repo.entries.values() if e.kind == "import"]
    assert (entry.actor_subject, entry.path) == (OWNER, None)
    assert {s["ref"] for s in found.sources.values()} == {f"record:{entry.id}"}
    assert entry.text.startswith('{"cells":') and '"digest":"sha256:' in entry.text
    # 导入的一份可以直接出一版，编出来的方法层与原来的一样
    draft = await works.publish(OWNER, str(other.id))
    assert draft.manifest["pack"]["workflow"] == first.manifest["pack"]["workflow"]
    with pytest.raises(BadRequestError) as caught:
        await works.import_manifest(
            OWNER, str(other.id), revision=1, manifest={"schema": "knowbot-manifest/v1"}
        )
    assert caught.value.code == "manifest_invalid"


@pytest.mark.asyncio
async def test_copy_only_from_my_own_drafts(wired) -> None:
    repo, works, svc, _ = wired
    mine = await _request(svc)
    await works.save(OWNER, str(mine.id), revision=0, edits=CELLS)
    source = await works.publish(OWNER, str(mine.id))
    target = await _request(svc)
    found, _ = await works.copy_from(
        OWNER, str(target.id), revision=0, draft_id=str(source.id)
    )
    (entry,) = [e for e in repo.entries.values() if e.kind == "copy"]
    bundle = json.loads(entry.text)
    assert bundle["from"] == f"draft:{source.id}"
    assert set(bundle["cells"]) == set(found.sources)
    assert {s["ref"] for s in found.sources.values()} == {f"record:{entry.id}"}
    assert found.body["steps"][0]["rules"][0]["text"] == "合并、母公司都有时问用户。"
    # 别人的准专家不行，与没有的一样
    theirs = await accepted_request(svc, "someone-else")
    other = await platform_draft(svc, str(theirs.id), manifest())
    for draft_id in (str(other.id), str(uuid.uuid4())):
        with pytest.raises(NotFoundError):
            await works.copy_from(OWNER, str(target.id), revision=1, draft_id=draft_id)
