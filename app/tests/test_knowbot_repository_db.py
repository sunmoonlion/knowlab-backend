"""分身提交与草稿在真 PostgreSQL 上（一次性 schema）。

真迁移、真仓储、状态约束、并发建草稿。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryDataStore,
    manifest,
    request_body,
    result,
    waiting,
)

ROOT = Path(__file__).resolve().parents[1]


def migrate(connection):
    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted((ROOT / "alembic/versions").glob("20*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("DELIVERY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DELIVERY_TEST_DATABASE_URL to a disposable *_tests database")
    parsed = make_url(url)
    assert parsed.database and parsed.database.endswith("_tests")
    schema = "knowlab_test_" + uuid.uuid4().hex
    engine = create_async_engine(
        parsed.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    try:
        async with engine.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{schema}"'))
            await c.execute(
                text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public')
            )
            await c.run_sync(migrate)
        yield make_session_factory(engine)
    finally:
        async with engine.begin() as c:
            await c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


RUNTIME, KNOWMART = FakeRuntime(), FakeKnowmart()


def service(session) -> KnowbotCreationService:
    return KnowbotCreationService(SqlKnowlabRepository(session), RUNTIME, KNOWMART)


@pytest.mark.asyncio
async def test_submit_draft_test_and_deliver_round_trip(db) -> None:
    async with db() as session:
        request = await service(session).submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
    async with db() as session:
        draft = await service(session).add_draft(str(request.id), manifest())
    async with db() as session:
        svc = service(session)
        await svc.review_rules(str(draft.id), "admin-1")
        run = await svc.start_test(
            str(draft.id), budget_limit="20", timeout_seconds=600
        )
    RUNTIME.results[run.run_id] = result(run.run_id, "passed")
    async with db() as session:
        done = await service(session).refresh(str(draft.id))
    assert done.status == "delivered"
    assert done.report and done.report["cases"][0]["status"] == "passed"
    assert done.delivery and done.delivery["knowbot_id"] == "kb-1"
    async with db() as session:
        [(mine, drafts)] = await service(session).my_requests(OWNER)
        assert mine.idea["kind"] == "six_questions"
        assert mine.cases[0]["case_id"] == "c1"
        assert [d.status for d in drafts] == ["delivered"]
        assert drafts[0].rules_reviewed_by == "admin-1"
        assert await service(session).my_requests("someone-else") == []


@pytest.mark.asyncio
async def test_runtimes_find_the_delivered_and_the_testing_draft(db) -> None:
    """运行方读数据时按交付记录与 run_id 找草稿（0027 §3.5）。

    JSONB 取值的查询在真库上对。
    """
    async with db() as session:
        request = await service(session).submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
    async with db() as session:
        draft = await service(session).add_draft(str(request.id), manifest())
    async with db() as session:
        svc = service(session)
        await svc.review_rules(str(draft.id), "admin-1")
        run = await svc.start_test(
            str(draft.id), budget_limit="20", timeout_seconds=600
        )
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        testing = await repo.find_testing_draft(run.run_id)
        assert testing is not None and testing.id == draft.id
        assert await repo.find_testing_draft("knowlab-run-none") is None
        assert await repo.find_delivered_draft("kb-1", "1") is None
    RUNTIME.results[run.run_id] = result(run.run_id, "passed")
    async with db() as session:
        await service(session).refresh(str(draft.id))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        assert await repo.find_testing_draft(run.run_id) is None
        found = await repo.find_delivered_draft("kb-1", "1")
        assert found is not None and found.id == draft.id
        assert await repo.find_delivered_draft("kb-1", "2") is None
        assert await repo.find_delivered_draft("kb-2", "1") is None


@pytest.mark.asyncio
async def test_the_database_refuses_unknown_states_and_duplicate_revisions(
    db,
) -> None:
    async with db() as session:
        request = await service(session).submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
        draft = await service(session).add_draft(str(request.id), manifest())
    for statement in (
        f"UPDATE knowbot_draft SET status = 'approved' WHERE id = '{draft.id}'",
        "INSERT INTO knowbot_draft (id, request_id, revision, version, status, "
        f"manifest, run_attempts) VALUES ('{uuid.uuid4()}', '{request.id}', 1, '1', "
        "'pending_test', '{}'::jsonb, 0)",
        f"DELETE FROM knowbot_request WHERE id = '{request.id}'",
    ):
        async with db() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(statement))


@pytest.mark.asyncio
async def test_concurrent_drafts_for_one_request_leave_only_one(db) -> None:
    async with db() as session:
        request = await service(session).submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )

    async def add() -> object:
        async with db() as session:
            return await service(session).add_draft(str(request.id), manifest())

    results = await asyncio.gather(add(), add(), return_exceptions=True)
    conflicts = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(conflicts) == 1, results
    async with db() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM knowbot_draft"))
        ).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_data_is_per_owner_and_bound_data_stays(db) -> None:
    store = MemoryDataStore()
    async with db() as session:
        data = KnowbotDataService(SqlKnowlabRepository(session), store)
        mine = await data.upload(OWNER, "年报.pdf", b"%PDF-1.7 a")
        await data.upload("someone-else", "别人的.pdf", b"%PDF-1.7 b")
        found, used = await data.mine(OWNER)
        assert [d.id for d in found] == [mine.id] and used == len(b"%PDF-1.7 a")
    async with db() as session:
        svc = service(session)
        request = await svc.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
        binding = [{"dataset_id": str(mine.id), "version": "1", "sha256": mine.sha256}]
        await svc.add_draft(str(request.id), manifest(data_bindings=binding))
    async with db() as session:
        data = KnowbotDataService(SqlKnowlabRepository(session), store)
        with pytest.raises(ConcurrencyConflictError):
            await data.delete(OWNER, str(mine.id))  # 在做的草稿绑着（JSONB 包含查询）


@pytest.mark.asyncio
async def test_questions_are_stored_once_and_routes_respect_their_kind(db) -> None:
    from app.application.services.knowbot_questions import (
        AnswerIn,
        KnowbotQuestionService,
    )

    async with db() as session:
        svc = service(session)
        request = await svc.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
        draft = await svc.add_draft(str(request.id), manifest())
        await svc.review_rules(str(draft.id), "admin-1")
        run = await svc.start_test(
            str(draft.id), budget_limit="20", timeout_seconds=600
        )
    RUNTIME.results[run.run_id] = waiting(run.run_id)
    for _ in range(2):
        async with db() as session:
            await service(session).refresh(str(draft.id))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        found = await repo.list_questions(draft_id=draft.id, open_only=True)
        assert sorted(q.question_id for q in found) == [
            "q-approve",
            "q-judge",
            "q-scope",
        ]
        scope = next(q for q in found if q.key == "period")
        approve = next(q for q in found if q.kind == "tool_approval")
        # 先归谁（审读第 2 条）：审批归我们，别的建出来就归用户
        assert approve.route is None and scope.route == "user"
        asks = KnowbotQuestionService(repo, RUNTIME)
        await asks.answer_as_user(OWNER, str(scope.id), AnswerIn(option_id="3y"))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        stored = await repo.get_request(request.id)
        assert stored and stored.cases[0]["input"]["answers"] == {"period": "3y"}
        judge = next(
            q
            for q in await repo.list_questions(draft_id=draft.id, open_only=True)
            if q.question_id == "q-judge"
        )
        await KnowbotQuestionService(repo, RUNTIME).take(str(judge.id), "跑法问题")
    async with db() as session:
        taken = await SqlKnowlabRepository(session).get_question(judge.id)
        assert taken and (taken.route, taken.route_reason) == ("operator", "跑法问题")
    # 库里的约束兜底：审批不能转给谁；改派给我们必须写理由
    for sql, q in [
        ("UPDATE knowbot_question SET route = 'user' WHERE id = :q", approve.id),
        (
            "UPDATE knowbot_question SET route_reason = NULL WHERE id = :q",
            judge.id,
        ),
        ("UPDATE knowbot_question SET route = NULL WHERE id = :q", judge.id),
    ]:
        async with db() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(sql), {"q": str(q)})


def _step(name: str, direction: str):
    """只跑一条迁移的 upgrade 或 downgrade。"""

    def run(connection) -> None:
        path = ROOT / "alembic/versions" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, direction)()

    return run


@pytest.mark.asyncio
async def test_the_route_migration_moves_rows_that_were_already_there(db) -> None:
    """0007 上线时库里已有的问题：没答的转给用户，运营直接答过的记成运营改派的。"""
    async with db() as session:
        svc = service(session)
        request = await svc.submit(
            OWNER, KnowbotRequestIn.model_validate(request_body())
        )
        draft = await svc.add_draft(str(request.id), manifest())
        await svc.review_rules(str(draft.id), "admin-1")
        run = await svc.start_test(
            str(draft.id), budget_limit="20", timeout_seconds=600
        )
    RUNTIME.results[run.run_id] = waiting(run.run_id)
    async with db() as session:
        await service(session).refresh(str(draft.id))
    name = "20261018_0007_question_route"
    async with db() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(_step(name, "downgrade"))
        # 0006 时的样子：路由都空着，有一条是运营直接答的
        await session.execute(text("UPDATE knowbot_question SET route = NULL"))
        await session.execute(
            text(
                'UPDATE knowbot_question SET answer = \'{"option_id": "3y"}\','
                " answered_by = 'operator' WHERE question_id = 'q-scope'"
            )
        )
        await connection.run_sync(_step(name, "upgrade"))
    async with db() as session:
        rows = await session.execute(
            text("SELECT question_id, route, route_reason FROM knowbot_question")
        )
        assert {r[0]: (r[1], r[2]) for r in rows} == {
            "q-approve": (None, None),
            "q-scope": ("operator", "迁移前运营直接答的"),
            "q-judge": ("user", None),
        }
