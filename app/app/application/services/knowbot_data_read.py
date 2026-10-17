"""运行方读用户数据（SDD 0027 §3.5，契约 knowbot-data/v1）：knowlab 这一道核。

运行方已经按自己的委托核过一次；这里按 knowlab 自己的记录再核一次，两道都过才给：
- 调用方必须是这份草稿登记的运行方；
- 分身：所有者、分身 id、版本、清单摘要和交付记录对得上；测试：这次测试正在跑；
- 数据在这一版清单的 data_bindings 里、属于草稿所有者、没删、版本与 sha256 一致。
不符一律「没有」（404），不说是哪一条没过。

按格子读（SDD 0030 §3.1）走同一套核；不是表格的数据回 422，没有那一行、那一列照实标出。
"""

from __future__ import annotations

import uuid

from app.application.errors.exceptions import (
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.application.ports.knowbots import (
    DatasetRecord,
    DataStore,
    DraftRecord,
    KnowlabRepository,
    RequestRecord,
    TableExtractor,
    TextExtractor,
)
from app.domain.knowbots import datasets
from app.domain.knowbots.data_read import (
    CellsRequestV1,
    CellsResultV1,
    CellValue,
    Citation,
    DatasetEntry,
    KnowbotPurpose,
    ListRequestV1,
    ListResultV1,
    ReadRequestV1,
    ReadResultV1,
    RunPurpose,
    TableRequestV1,
    TableResultV1,
    TableRow,
    Tables,
    TableUnreadable,
    paginate,
    table_page,
)


def _missing() -> NotFoundError:
    return NotFoundError("No such data", code="knowbot_data_not_found")


class KnowbotDataReader:
    def __init__(
        self,
        repo: KnowlabRepository,
        store: DataStore,
        extractor: TextExtractor,
        tables: TableExtractor,
    ) -> None:
        self.repo = repo
        self.store = store
        self.extractor = extractor
        self.tables = tables

    async def list(self, caller: str, request: ListRequestV1) -> ListResultV1:
        owner, bound = await self._scope(caller, request.purpose)
        found = []
        for dataset_id in bound:
            record = await self._dataset(owner, bound, dataset_id)
            if record is not None:
                found.append(
                    DatasetEntry(
                        dataset_id=str(record.id),
                        name=record.name,
                        content_type=record.content_type,
                        size_bytes=record.size_bytes,
                        version=record.version,
                        sha256=record.sha256,
                    )
                )
        return ListResultV1(datasets=tuple(found))

    async def read(self, caller: str, request: ReadRequestV1) -> ReadResultV1:
        owner, bound = await self._scope(caller, request.purpose)
        record = await self._dataset(owner, bound, request.dataset_id)
        if record is None:
            raise _missing()
        data = await self._content(record)
        extracted = await self.extractor.extract(
            data=data, content_type=record.content_type, sha256=record.sha256
        )
        text, pages = paginate(extracted.text, request.page)
        page = request.page
        note = extracted.note
        if page > pages:
            note = f"There are only {pages} pages."
        return ReadResultV1(
            dataset_id=str(record.id),
            name=record.name,
            content_type=record.content_type,
            page=page,
            pages=pages,
            text=text,
            truncated=extracted.truncated,
            note=note,
            citation=Citation(
                dataset_id=str(record.id),
                version=record.version,
                sha256=record.sha256,
                page=page,
                pages=pages,
            ),
        )

    async def table(self, caller: str, request: TableRequestV1) -> TableResultV1:
        owner, bound = await self._scope(caller, request.purpose)
        record = await self._dataset(owner, bound, request.dataset_id)
        if record is None:
            raise _missing()
        tables = await self._tables(record)
        sheet = tables.sheet(request.sheet)
        if sheet is None:
            raise ValidationError(
                f"There is no sheet {request.sheet!r}"
                if request.sheet
                else "The data has no sheet",
                code="knowbot_data_no_sheet",
            )
        rows, pages = table_page(sheet, request.page)
        return TableResultV1(
            dataset_id=str(record.id),
            name=record.name,
            content_type=record.content_type,
            sheets=tuple(s.name for s in tables.sheets),
            sheet=sheet.name,
            columns=sheet.columns,
            page=request.page,
            pages=pages,
            rows=tuple(
                TableRow(
                    label=row.label,
                    values={c: cell.raw for c, cell in row.cells.items() if cell.raw},
                )
                for row in rows
            ),
            note=f"There are only {pages} pages." if request.page > pages else None,
            citation=Citation(
                dataset_id=str(record.id),
                version=record.version,
                sha256=record.sha256,
                page=request.page,
                pages=pages,
            ),
        )

    async def cells(self, caller: str, request: CellsRequestV1) -> CellsResultV1:
        owner, bound = await self._scope(caller, request.purpose)
        records: dict[str, DatasetRecord] = {}
        loaded: dict[str, Tables] = {}
        for ref in request.cells:
            if ref.dataset_id in records:
                continue
            record = await self._dataset(owner, bound, ref.dataset_id)
            if record is None:
                raise _missing()
            records[ref.dataset_id] = record
        for dataset_id, record in records.items():
            loaded[dataset_id] = await self._tables(record)
        found = []
        for ref in request.cells:
            record = records[ref.dataset_id]
            sheet = loaded[ref.dataset_id].sheet(ref.sheet)
            row = sheet.row(ref.row) if sheet else None
            cell = row.cells.get(ref.column) if row else None
            known = (
                sheet is not None and row is not None and ref.column in sheet.columns
            )
            found.append(
                CellValue(
                    dataset_id=ref.dataset_id,
                    sheet=ref.sheet,
                    row=ref.row,
                    column=ref.column,
                    found=known,
                    raw=(cell.raw if cell else "") if known else None,
                    number=cell.number if cell else None,
                    version=record.version,
                    sha256=record.sha256,
                )
            )
        return CellsResultV1(cells=tuple(found))

    async def _content(self, record: DatasetRecord) -> bytes:
        if not self.store.enabled:
            raise ServiceUnavailableError(
                "Reading data is not available yet", code="storage_unavailable"
            )
        try:
            return await self.store.get(
                object_key=record.object_key,
                version_id=record.version_id,
                sha256=record.sha256,
                max_bytes=datasets.MAX_FILE_BYTES,
            )
        except RuntimeError as exc:
            # 存储里的内容对不上登记的摘要：不给内容，也不把原因细节给出去
            raise ServiceUnavailableError(
                "The data cannot be read now", code="knowbot_data_unavailable"
            ) from exc

    async def _tables(self, record: DatasetRecord) -> Tables:
        data = await self._content(record)
        try:
            return await self.tables.tables(
                data=data, content_type=record.content_type, sha256=record.sha256
            )
        except TableUnreadable as exc:
            raise ValidationError(
                f"{record.name} cannot be read as a table: {exc}",
                code="knowbot_data_not_a_table",
            ) from exc

    async def _scope(
        self, caller: str, purpose: KnowbotPurpose | RunPurpose
    ) -> tuple[str, dict[str, tuple[str, str]]]:
        """这次能读谁的、哪几份：（草稿所有者，数据 id → （版本，sha256））。"""
        if isinstance(purpose, KnowbotPurpose):
            draft = await self.repo.find_delivered_draft(
                purpose.knowbot_id, purpose.version
            )
            delivery = (draft.delivery or {}) if draft else {}
            if (
                draft is None
                or delivery.get("knowbot_id") != purpose.knowbot_id
                or str(delivery.get("version")) != purpose.version
                or delivery.get("digest") != purpose.digest
            ):
                raise _missing()
            request = await self._request(draft, caller)
            if request.owner_subject != purpose.owner_subject:
                raise _missing()
        else:
            draft = await self.repo.find_testing_draft(purpose.run_id)
            if draft is None or draft.run_id != purpose.run_id:
                raise _missing()
            request = await self._request(draft, caller)
        return request.owner_subject, _bindings(draft)

    async def _request(self, draft: DraftRecord, caller: str) -> RequestRecord:
        request = await self.repo.get_request(draft.request_id)
        if request is None or request.runtime != caller:
            raise _missing()
        return request

    async def _dataset(
        self, owner: str, bound: dict[str, tuple[str, str]], dataset_id: str
    ) -> DatasetRecord | None:
        if dataset_id not in bound:
            return None
        try:
            record = await self.repo.get_dataset(uuid.UUID(dataset_id))
        except ValueError:
            return None
        if (
            record is None
            or record.owner_subject != owner
            or record.deleted_at is not None
            or (record.version, record.sha256) != bound[dataset_id]
        ):
            return None
        return record


def _bindings(draft: DraftRecord) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    raw = draft.manifest.get("data_bindings")
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("dataset_id"):
            found[str(item["dataset_id"])] = (
                str(item.get("version")),
                str(item.get("sha256")),
            )
    return found
