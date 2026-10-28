"""工作稿：作者直接改、导入、复制自己的专家、预览、出一版（专家孵化方案第五、七、九至
十一节，decisions ⑮；第十五节第 1 步）。

只有作者本人（web端）：不是自己的名称与没有的一样，都是 404。

- 取：还没存过的名称回一份空的（revision 0），第一次存时才建。
- 存：一次可以改几格，带上读到的 revision，对不上回 409。有字的格子改了的，每格记一条
  编辑记录，出处记 `user_text`、引这条记录；字没变的格子出处照旧（接受过的建议不会因为
  挪了顺序就变成作者自己写的，反过来也一样）。
- 导入专家包、复制自己的准专家：整份换掉，记一条导入 / 复制记录，拆出的每一格引它。
- 出一版：方法层每格都要有出处、核得上；编出的清单过 knowlab 的草稿检查与 pack 结构
  自检；都过了照版本号规矩建准专家（自助，出处由程序核过）。工作稿本身不变，可以接着改
  再出一版。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    DraftRecord,
    KnowlabRepository,
    RequestRecord,
    WorkdraftRecord,
)
from app.application.services.knowbot_service import parse_id
from app.domain.knowbots.compile import compile_manifest, decompile, pack_problems
from app.domain.knowbots.drafts import draft_manifest_problems
from app.domain.knowbots.workdraft import (
    MAX_BODY_BYTES,
    MAX_EDITS,
    Problem,
    WorkdraftBody,
    apply,
    bundle_text,
    canonical,
    cells,
    empty_body,
    record_ids,
    size_of,
    source_problems,
)

AUTHOR = "author"
# 自助出的一版：出处由程序在出版本时逐格核过（第 9.2 节），记在「核出处」那一栏
PROGRAM_REVIEWER = "system:workdraft"
MAX_PROBLEMS_SHOWN = 20


def _summary(problems: Sequence[Problem]) -> str:
    shown = [f"{p.path or 'manifest'}: {p.message}" for p in problems]
    more = len(shown) - MAX_PROBLEMS_SHOWN
    text = "; ".join(shown[:MAX_PROBLEMS_SHOWN])
    return text + (f"; and {more} more" if more > 0 else "")


def _body(raw: Any) -> WorkdraftBody:
    try:
        return WorkdraftBody.model_validate(raw)
    except ValidationError as exc:
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise BadRequestError(
            "The workdraft is invalid at " + ", ".join(where[:8]),
            code="workdraft_invalid",
        ) from None


class KnowbotWorkdraftService:
    def __init__(
        self,
        repo: KnowlabRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.now = now

    async def _request(self, owner_subject: str, request_id: str) -> RequestRecord:
        request = await self.repo.get_request(parse_id(request_id, "request"))
        if request is None or request.owner_subject != owner_subject:
            raise NotFoundError("No such request", code="request_not_found")
        return request

    async def get(
        self, owner_subject: str, request_id: str
    ) -> tuple[RequestRecord, WorkdraftRecord | None]:
        """自己的名称的工作稿；还没存过的回 None（页面当空的一份，revision 0）。"""
        request = await self._request(owner_subject, request_id)
        return request, await self.repo.get_workdraft(request.id)

    async def _locked(self, request: RequestRecord, revision: int) -> WorkdraftRecord:
        """锁住这个名称，核 revision；还没有的建一份空的（在同一个事务里）。"""
        await self.repo.lock_request(request.id)
        current = await self.repo.get_workdraft(request.id, for_update=True)
        have = 0 if current is None else current.revision
        if revision != have:
            raise ConcurrencyConflictError(
                "The workdraft changed since it was read; reload it",
                code="workdraft_revision",
            )
        if current is None:
            current = await self.repo.create_workdraft(
                request_id=request.id,
                owner_subject=request.owner_subject,
                body=empty_body(),
            )
        return current

    async def _store(
        self, current: WorkdraftRecord, body: WorkdraftBody, sources: dict[str, Any]
    ) -> WorkdraftRecord:
        doc = body.model_dump(mode="json")
        if size_of(doc) > MAX_BODY_BYTES:
            raise BadRequestError(
                f"The workdraft is larger than {MAX_BODY_BYTES} bytes",
                code="workdraft_too_large",
            )
        await self.repo.update_workdraft(
            current.id, body=doc, sources=sources, revision=current.revision + 1
        )
        found = await self.repo.get_workdraft(current.request_id)
        assert found is not None
        return found

    # ---------- 作者直接改 ----------
    async def save(
        self,
        owner_subject: str,
        request_id: str,
        *,
        revision: int,
        edits: Sequence[tuple[str, Any]],
    ) -> WorkdraftRecord:
        """存几格（path, value）：按顺序改，改完整份核一次形状。"""
        if not edits or len(edits) > MAX_EDITS:
            raise BadRequestError(
                f"Save 1 to {MAX_EDITS} cells at a time", code="workdraft_invalid"
            )
        request = await self._request(owner_subject, request_id)
        async with self.repo.transaction():
            current = await self._locked(request, revision)
            doc: dict[str, Any] = current.body
            for path, value in edits:
                try:
                    doc = apply(doc, path, value)
                except ValueError as exc:
                    raise BadRequestError(
                        f"Cannot save {path[:80]}: {exc}", code="workdraft_path_invalid"
                    ) from None
            body = _body(doc)
            before, after = cells(_body(current.body)), cells(body)
            sources = {p: s for p, s in current.sources.items() if p in after}
            for path, text in after.items():
                if before.get(path) == text and path in sources:
                    continue
                entry = await self.repo.add_workdraft_entry(
                    workdraft_id=current.id,
                    kind="edit",
                    actor_subject=owner_subject,
                    actor_role=AUTHOR,
                    path=path,
                    text=text,
                )
                sources[path] = {"kind": "user_text", "ref": f"record:{entry.id}"}
            return await self._store(current, body, sources)

    # ---------- 导入、复制 ----------
    async def _replace(
        self,
        owner_subject: str,
        request: RequestRecord,
        *,
        revision: int,
        manifest: object,
        kind: str,
        origin: str,
    ) -> tuple[WorkdraftRecord, list[str]]:
        try:
            body, notes = decompile(manifest)
        except ValueError as exc:
            raise BadRequestError(str(exc)[:500], code="manifest_invalid") from None
        async with self.repo.transaction():
            current = await self._locked(request, revision)
            found = cells(body)
            extra = {}
            if kind == "import":
                digest = hashlib.sha256(canonical(manifest).encode("utf-8"))
                extra["digest"] = "sha256:" + digest.hexdigest()
            entry = await self.repo.add_workdraft_entry(
                workdraft_id=current.id,
                kind=kind,
                actor_subject=owner_subject,
                actor_role=AUTHOR,
                text=bundle_text(origin, found, **extra),
            )
            ref = {"kind": "user_text", "ref": f"record:{entry.id}"}
            stored = await self._store(current, body, {path: ref for path in found})
        return stored, notes

    async def import_manifest(
        self, owner_subject: str, request_id: str, *, revision: int, manifest: object
    ) -> tuple[WorkdraftRecord, list[str]]:
        """导入专家包（第十节）：方法层算作者原话（页面上照实写明），工程层留进「高级」。"""
        request = await self._request(owner_subject, request_id)
        return await self._replace(
            owner_subject,
            request,
            revision=revision,
            manifest=manifest,
            kind="import",
            origin="import",
        )

    async def copy_from(
        self, owner_subject: str, request_id: str, *, revision: int, draft_id: str
    ) -> tuple[WorkdraftRecord, list[str]]:
        """拿作者自己名下的一位准专家（哪个名称的都行）作起点；别人的与没有的一样，404。"""
        request = await self._request(owner_subject, request_id)
        draft = await self.repo.get_draft(parse_id(draft_id))
        source = (
            None if draft is None else await self.repo.get_request(draft.request_id)
        )
        if draft is None or source is None or source.owner_subject != owner_subject:
            raise NotFoundError("No such draft", code="draft_not_found")
        return await self._replace(
            owner_subject,
            request,
            revision=revision,
            manifest=draft.manifest,
            kind="copy",
            origin=f"draft:{draft.id}",
        )

    # ---------- 预览、出一版 ----------
    async def _next(self, request: RequestRecord) -> tuple[str, int]:
        """（版本号 x = 已有专家的最大版本号 + 1，修订号 y 在这个名称里递增）。"""
        drafts = await self.repo.list_drafts(request_id=request.id)
        experts = await self.repo.list_experts([request.id])
        version = str(max((e.version for e in experts), default=0) + 1)
        return version, max((d.revision for d in drafts), default=0) + 1

    async def _answered(self, request: RequestRecord) -> set[str]:
        """这个名称里作者本人答过的问题（缺口、测试中的判断）。"""
        found: set[str] = set()
        for draft in await self.repo.list_drafts(request_id=request.id):
            for q in await self.repo.list_questions(draft_id=draft.id):
                if (
                    q.answered_by == "user"
                    and q.answer is not None
                    and q.kind != "tool_approval"
                ):
                    found.add(str(q.id))
        return found

    async def _compile(
        self, request: RequestRecord, current: WorkdraftRecord | None, version: str
    ) -> tuple[dict[str, Any], list[Problem]]:
        body = _body(empty_body() if current is None else current.body)
        sources = {} if current is None else current.sources
        entries = await self.repo.list_workdraft_entries(record_ids(sources))
        problems = source_problems(
            body,
            sources,
            workdraft_id=None if current is None else current.id,
            owner_subject=request.owner_subject,
            entries={e.id: e for e in entries},
            answered=await self._answered(request),
        )
        manifest, compiled = compile_manifest(
            body,
            sources,
            pack_id=f"knowlab-{request.id}",
            version=version,
            runtime=request.runtime,
            fallback_name=request.title,
        )
        problems += compiled
        problems += [
            Problem("", p)
            for p in draft_manifest_problems(
                manifest, runtime=request.runtime, version=version
            )
        ]
        problems += pack_problems(manifest["pack"])
        return manifest, problems

    async def preview(
        self, owner_subject: str, request_id: str
    ) -> tuple[dict[str, Any], list[Problem], str]:
        """编出的清单与出不了版本的地方（不出版本）：（清单，问题，会出的版本号）。"""
        request, current = await self.get(owner_subject, request_id)
        version, _ = await self._next(request)
        manifest, problems = await self._compile(request, current, version)
        return manifest, problems, version

    async def publish(self, owner_subject: str, request_id: str) -> DraftRecord:
        """出一版（第十一节）：把关都过了才出准专家第 n 版（只增），记自助。"""
        request = await self._request(owner_subject, request_id)
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            current = await self.repo.get_workdraft(request.id, for_update=True)
            if current is None:
                raise BadRequestError(
                    "The workdraft is empty", code="workdraft_invalid"
                )
            fresh = await self.repo.get_request(request.id)
            assert fresh is not None
            version, revision = await self._next(fresh)
            manifest, problems = await self._compile(fresh, current, version)
            if problems:
                raise BadRequestError(_summary(problems), code="workdraft_invalid")
            draft = await self.repo.create_draft(
                request_id=fresh.id,
                request_version=fresh.latest_version,
                revision=revision,
                version=version,
                manifest=manifest,
                mode="self",
            )
            await self.repo.update_draft(
                draft.id,
                rules_reviewed_by=PROGRAM_REVIEWER,
                rules_reviewed_at=self.now(),
            )
        found = await self.repo.get_draft(draft.id)
        assert found is not None
        return found
