"""Exercise Studio deletion through HTTP and a migrated PostgreSQL database."""

from uuid import UUID

import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.application.services.method_drafts import (
    CreateMethodDraftIn,
    MethodDraftService,
)
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.interfaces.endpoints.knowbot_routes import get_db_session
from app.main import app
from tests.knowbot_fakes import OWNER
from tests.test_knowbot_repository_db import db as db
from tests.test_knowbot_workdraft_routes import WEB, WEB_SID, client
from tests.test_knowbot_workdraft_routes import wired as wired


@pytest.mark.asyncio
async def test_delete_only_selected_draft_and_keep_evidence_and_children(
    db, wired, monkeypatch
):
    async def session_dependency():
        async with db() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db_session, session_dependency)
    path = "/api/web/v1/method-drafts"
    body = {
        "archetype": "S1",
        "method_kind": "expert",
        "title": "同名草稿",
        "purpose": "核对",
    }
    async with client() as c:
        c.cookies.set(WEB_SID, "member")
        created = (await c.post(path, json=body, headers=WEB)).json()
        mid = UUID(created["method_id"])
        sibling = (await c.post(path, json=body, headers=WEB)).json()["method_id"]
        url = f"{path}/{mid}"
        rev = {"revision": created["revision"]}
        async with db() as s:
            repo = SqlKnowlabRepository(s)
            svc = MethodDraftService(repo)
            other = await svc.create("another-author", CreateMethodDraftIn(**body))
            anchor = await svc.ensure_runtime_anchor(OWNER, str(mid))
            child = await svc.create(OWNER, CreateMethodDraftIn(**body))
            row = await repo.get_method_workdraft(UUID(child["method_id"]))
            async with repo.transaction():
                await repo.update_workdraft(
                    row.id,
                    body=row.body,
                    sources=row.sources,
                    revision=row.revision,
                    parent_gap={"parent_method_id": str(mid), "step_id": "review"},
                )
                await repo.insert_method_version(
                    method_id=mid,
                    version=1,
                    digest="sha256:" + "a" * 64,
                    command_digest="sha256:" + "b" * 64,
                    definition={},
                    provenance={},
                    source_workdraft_id=UUID(created["workdraft_id"]),
                )

        assert (await c.delete(url, params=rev)).status_code == 403
        assert (
            await c.delete(f"{path}/{other['method_id']}", params=rev, headers=WEB)
        ).status_code == 404
        assert (
            await c.delete(url, params={"revision": 0}, headers=WEB)
        ).status_code == 409
        assert (await c.get(url)).status_code == 200
        for _ in range(2):
            assert (await c.delete(url, params=rev, headers=WEB)).status_code == 204
        assert (await c.get(url)).status_code == 404
        assert (
            await c.patch(
                url,
                json={"revision": 1, "elements": {"purpose": "旧标签"}},
                headers=WEB,
            )
        ).status_code == 404
        items = (await c.get(path)).json()["items"]
        assert {r["method_id"] for r in items} == {sibling, child["method_id"]}
        kept_child = next(r for r in items if r["method_id"] == child["method_id"])
        assert kept_child["parent_gap"] is None and kept_child["revision"] == 2

    async with db() as s:
        repo = SqlKnowlabRepository(s)
        retired = await repo.get_method_workdraft(mid)
        assert retired.body == retired.sources == retired.data_formats == {}
        assert retired.htn_state is None and retired.parent_gap is None
        assert (await repo.method_version(mid, 1)).digest == "sha256:" + "a" * 64
        with pytest.raises(NotFoundError):
            await KnowbotWorkdraftService(repo).get(OWNER, str(anchor.id))
