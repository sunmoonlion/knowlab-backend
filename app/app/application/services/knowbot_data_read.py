"""运行方读用户数据（SDD 0027 §3.5，契约 knowbot-data/v1）：knowlab 这一道核。

运行方已经按自己的委托核过一次；这里按 knowlab 自己的记录再核一次，两道都过才给：
- 调用方必须是这份草稿登记的运行方；
- 专家：上架了没停用、所有者、knowmart 的专家 id、版本、清单摘要对得上（SDD 0037）；
  测试：这次测试正在跑、没签测试运行授权，数据还没搬进 knowbase（SDD 0042）；
- 数据在这一版清单的 data_bindings 里、属于草稿所有者、没删、版本与 sha256 一致。
不符一律「没有」（404），不说是哪一条没过。

按格子读（SDD 0030 §3.1）走同一套核；不是表格的数据回 422，没有那一行、那一列照实标出。
核引文（SDD 0036）也走同一套核；没找到的照实标出原因。
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
    QuoteFound,
    QuotesRequestV1,
    QuotesResultV1,
    ReadRequestV1,
    ReadResultV1,
    RunPurpose,
    TableRequestV1,
    TableResultV1,
    TableRow,
    Tables,
    TableUnreadable,
    find_quote,
    paginate,
    pdf_pages,
    table_page,
)
from app.domain.knowbots.lifecycle import ACTIVE_TESTS

PDF = "application/pdf"


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
        owner, bound, tested = await self._scope(caller, request.purpose)
        found = []
        for dataset_id in bound:
            record = await self._dataset(owner, bound, dataset_id, tested)
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
        owner, bound, tested = await self._scope(caller, request.purpose)
        record = await self._dataset(owner, bound, request.dataset_id, tested)
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
        owner, bound, tested = await self._scope(caller, request.purpose)
        record = await self._dataset(owner, bound, request.dataset_id, tested)
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
        owner, bound, tested = await self._scope(caller, request.purpose)
        records: dict[str, DatasetRecord] = {}
        loaded: dict[str, Tables] = {}
        for ref in request.cells:
            if ref.dataset_id in records:
                continue
            record = await self._dataset(owner, bound, ref.dataset_id, tested)
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

    async def quotes(self, caller: str, request: QuotesRequestV1) -> QuotesResultV1:
        owner, bound, tested = await self._scope(caller, request.purpose)
        pages: dict[str, dict[int, str]] = {}
        truncated: set[str] = set()  # 抽取被截断的
        records: dict[str, DatasetRecord] = {}
        for ref in request.quotes:
            if ref.dataset_id in records:
                continue
            record = await self._dataset(owner, bound, ref.dataset_id, tested)
            if record is None:
                raise _missing()
            records[ref.dataset_id] = record
        for dataset_id, record in records.items():
            pages[dataset_id] = {}
            if record.content_type.split(";")[0].strip() == PDF:
                extracted = await self.extractor.extract(
                    data=await self._content(record),
                    content_type=record.content_type,
                    sha256=record.sha256,
                )
                pages[dataset_id] = pdf_pages(extracted.text)
                if extracted.truncated:
                    truncated.add(dataset_id)
        found = []
        for ref in request.quotes:
            record = records[ref.dataset_id]
            miss = find_quote(
                pages[ref.dataset_id],
                ref.page,
                ref.text,
                truncated=ref.dataset_id in truncated,
            )
            found.append(
                QuoteFound(
                    dataset_id=ref.dataset_id,
                    page=ref.page,
                    found=miss is None,
                    reason=miss,
                    version=record.version,
                    sha256=record.sha256,
                )
            )
        return QuotesResultV1(quotes=tuple(found))

    async def _content(self, record: DatasetRecord) -> bytes:
        if not self.store.enabled:
            raise ServiceUnavailableError(
                "Reading data is not available yet", code="storage_unavailable"
            )
        if record.object_key is None or record.version_id is None:
            # 只在 knowbase 的（2026-10-23 起新传的）：经 knowbase 的运行授权读，
            # 这里没有
            raise _missing()
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
    ) -> tuple[str, dict[str, tuple[str, str]], bool]:
        """这次能读谁的、哪几份：（所有者，数据 id → （版本，sha256），是不是测试）。

        测试经这里读只给有旧对象的（没搬进 knowbase 的，或搬了、本人还没补上测试
        同意的，SDD 0042 第七节「没搬完的」、Fable 评审 P2-4）：这次测试签了测试运行
        授权、或这一份只在 knowbase 里，就得凭授权向 knowbase 读，这里一律「没有」，
        授权被拒、撤了就绕不过去（Astra 评审 F02）。
        """
        if isinstance(purpose, KnowbotPurpose):
            # 上架了、没停用的那版专家（SDD 0037）；摘要要对得上 knowmart 的回执
            expert = await self.repo.find_listed_expert(
                purpose.knowbot_id, purpose.version
            )
            if (
                expert is None
                or expert.knowmart_knowbot_id != purpose.knowbot_id
                or str(expert.version) != purpose.version
                or expert.digest != purpose.digest
            ):
                raise _missing()
            draft = await self.repo.get_draft(expert.draft_id)
            if draft is None:
                raise _missing()
            request = await self._request(draft, caller)
            if request.owner_subject != purpose.owner_subject:
                raise _missing()
        else:
            test = await self.repo.find_test_by_run(purpose.run_id)
            if test is None or test.status not in ACTIVE_TESTS or test.grant_id:
                raise _missing()
            draft = await self.repo.get_draft(test.draft_id)
            if draft is None:
                raise _missing()
            request = await self._request(draft, caller)
        return (
            request.owner_subject,
            _bindings(draft),
            not isinstance(purpose, KnowbotPurpose),
        )

    async def _request(self, draft: DraftRecord, caller: str) -> RequestRecord:
        request = await self.repo.get_request(draft.request_id)
        if request is None or request.runtime != caller:
            raise _missing()
        return request

    async def _dataset(
        self,
        owner: str,
        bound: dict[str, tuple[str, str]],
        dataset_id: str,
        tested: bool,
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
            or (tested and record.item_id is not None and record.object_key is None)
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
