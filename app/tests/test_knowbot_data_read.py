"""运行方读用户数据（SDD 0027 §3.5，knowbot-data/v1）：knowlab 这一道核与抽文字。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.ports.knowbots import DatasetRecord
from app.application.services.knowbot_data_read import KnowbotDataReader
from app.domain.knowbots.data_read import (
    PAGE_CHARS,
    Extracted,
    ListRequestV1,
    ReadRequestV1,
    paginate,
)
from app.domain.knowbots.data_schema import DOCUMENTS, render
from app.domain.knowbots.datasets import XLSX
from app.infrastructure.documents import text_extract
from app.infrastructure.documents.text_extract import (
    NO_TEXT,
    UNREADABLE,
    CachedTextExtractor,
    extract_text,
)
from app.infrastructure.security.relation_auth import (
    ServiceCaller,
    require_read_service,
)
from app.interfaces.endpoints import knowbot_data_routes
from app.main import app
from tests.knowbot_fakes import OWNER, MemoryDataStore, MemoryKnowlab, manifest

KNOWBOT = "0b9b8c52-3f0e-4c34-9d0b-2b7f4d3c1a10"
INTERNAL = "/api/internal/v1/knowlab/knowbot-data"
CONTRACT_DIR = Path(__file__).resolve().parents[3] / "contracts/knowbot-data/v1"


class World:
    """一个用户、两份数据（一份绑定、一份没绑）、一版已交付、一版在测试。"""

    def __init__(self) -> None:
        self.repo = MemoryKnowlab()
        self.store = MemoryDataStore()
        self.reader = KnowbotDataReader(self.repo, self.store, CachedTextExtractor())

    async def setup(self) -> None:
        self.bound = await self._dataset(
            "年报.csv", "text/csv; charset=utf-8", "公司,营收\nA,100\n"
        )
        self.loose = await self._dataset(
            "别的.txt", "text/plain; charset=utf-8", "不该读到"
        )
        binding = {
            "dataset_id": str(self.bound.id),
            "version": "1",
            "sha256": self.bound.sha256,
        }
        self.request = await self.repo.create_request(
            owner_subject=OWNER, title="t", runtime="knowbench", idea={}, cases=[]
        )
        doc = manifest("1", data_bindings=[binding])
        self.digest = "sha256:" + "a" * 64
        self.delivered = await self.repo.create_draft(
            request_id=self.request.id, revision=1, version="1", manifest=doc
        )
        await self.repo.update_draft(
            self.delivered.id,
            status="delivered",
            delivery={"knowbot_id": KNOWBOT, "version": "1", "digest": self.digest},
        )
        self.testing = await self.repo.create_draft(
            request_id=self.request.id,
            revision=2,
            version="2",
            manifest=manifest("2", data_bindings=[binding]),
        )
        await self.repo.update_draft(
            self.testing.id, status="testing", run_id="knowlab-run-0002"
        )

    async def _dataset(self, name: str, content_type: str, text: str) -> DatasetRecord:
        data = text.encode("utf-8")
        record_id = uuid.uuid4()
        key = f"users/x/{record_id}"
        version_id, sha = await self.store.put(
            object_key=key, data=data, content_type=content_type
        )
        return await self.repo.create_dataset(
            DatasetRecord(
                id=record_id,
                owner_subject=OWNER,
                name=name,
                version="1",
                content_type=content_type,
                size_bytes=len(data),
                sha256=sha,
                object_key=key,
                version_id=version_id,
            )
        )

    def knowbot(self, **overrides: str) -> dict[str, str]:
        purpose = {
            "kind": "knowbot",
            "owner_subject": OWNER,
            "knowbot_id": KNOWBOT,
            "version": "1",
            "digest": self.digest,
        }
        return purpose | overrides

    def listing(self, purpose: dict[str, str]) -> ListRequestV1:
        return ListRequestV1.model_validate(
            {"contract": "knowbot-data/v1", "purpose": purpose}
        )

    def reading(
        self, purpose: dict[str, str], dataset: str, page: int = 1
    ) -> ReadRequestV1:
        return ReadRequestV1.model_validate(
            {
                "contract": "knowbot-data/v1",
                "purpose": purpose,
                "dataset_id": dataset,
                "page": page,
            }
        )


@pytest.fixture
async def world() -> World:
    w = World()
    await w.setup()
    return w


async def test_a_delivered_knowbot_lists_and_reads_only_its_bound_data(
    world: World,
) -> None:
    listed = await world.reader.list("knowbench", world.listing(world.knowbot()))
    assert [d.dataset_id for d in listed.datasets] == [str(world.bound.id)]
    read = await world.reader.read(
        "knowbench", world.reading(world.knowbot(), str(world.bound.id))
    )
    assert read.text == "公司,营收\nA,100\n"
    assert (read.page, read.pages, read.truncated, read.note) == (1, 1, False, None)
    assert read.citation.sha256 == world.bound.sha256 and read.citation.version == "1"


async def test_a_running_test_reads_the_same_way(world: World) -> None:
    purpose = {"kind": "test", "run_id": "knowlab-run-0002"}
    read = await world.reader.read(
        "knowbench", world.reading(purpose, str(world.bound.id))
    )
    assert read.name == "年报.csv"


@pytest.mark.parametrize(
    "change",
    [
        {"owner_subject": "someone-else"},
        {"digest": "sha256:" + "b" * 64},
        {"version": "2"},
        {"knowbot_id": "11111111-1111-4111-8111-111111111111"},
    ],
)
async def test_anything_off_from_the_delivery_is_not_found(
    world: World, change: dict[str, str]
) -> None:
    with pytest.raises(NotFoundError) as caught:
        await world.reader.list("knowbench", world.listing(world.knowbot(**change)))
    assert caught.value.code == "knowbot_data_not_found"


async def test_another_runtime_cannot_read(world: World) -> None:
    with pytest.raises(NotFoundError):
        await world.reader.list("other-runtime", world.listing(world.knowbot()))


async def test_unbound_or_deleted_or_changed_data_is_not_found(world: World) -> None:
    with pytest.raises(NotFoundError):
        await world.reader.read(
            "knowbench", world.reading(world.knowbot(), str(world.loose.id))
        )
    world.repo.datasets[world.bound.id] = replace(world.bound, sha256="c" * 64)
    with pytest.raises(NotFoundError):
        await world.reader.read(
            "knowbench", world.reading(world.knowbot(), str(world.bound.id))
        )
    world.repo.datasets[world.bound.id] = replace(
        world.bound, deleted_at=world.repo._now()
    )
    with pytest.raises(NotFoundError):
        await world.reader.read(
            "knowbench", world.reading(world.knowbot(), str(world.bound.id))
        )
    assert (
        await world.reader.list("knowbench", world.listing(world.knowbot()))
    ).datasets == ()


async def test_a_finished_or_other_test_cannot_read(world: World) -> None:
    for run_id in ("knowlab-run-9999", "knowlab-run-0002"):
        if run_id == "knowlab-run-0002":
            await world.repo.update_draft(world.testing.id, status="passed")
        with pytest.raises(NotFoundError):
            await world.reader.list(
                "knowbench", world.listing({"kind": "test", "run_id": run_id})
            )


async def test_pages_beyond_the_end_say_so(world: World) -> None:
    read = await world.reader.read(
        "knowbench", world.reading(world.knowbot(), str(world.bound.id), page=3)
    )
    assert (read.page, read.pages, read.text) == (3, 1, "")
    assert read.note == "There are only 1 pages."


def test_paginate_splits_at_the_page_size() -> None:
    text = "x" * (PAGE_CHARS * 2 + 5)
    assert paginate(text, 1) == ("x" * PAGE_CHARS, 3)
    assert paginate(text, 3) == ("x" * 5, 3)
    assert paginate("", 1) == ("", 1)


def test_requests_outside_the_contract_are_rejected() -> None:
    with pytest.raises(ValueError):
        ListRequestV1.model_validate(
            {"contract": "knowbot-data/v1", "purpose": {"kind": "all"}}
        )
    with pytest.raises(ValueError):
        ReadRequestV1.model_validate(
            {
                "contract": "knowbot-data/v1",
                "purpose": {"kind": "test", "run_id": "knowlab-run-1"},
                "dataset_id": "../etc/passwd",
            }
        )


# ---------- 抽文字 ----------


def _xlsx() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "利润表"
    sheet.append(["年份", "营收", None])
    sheet.append([2025, 100.0, None])
    book.create_sheet("说明").append(["单位：万元"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _pdf(text: str) -> bytes:
    """最小的一页 PDF，内容是一行 ASCII 字。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_excel_becomes_csv_per_sheet() -> None:
    got = extract_text(_xlsx(), XLSX)
    assert (
        got.text
        == "## Sheet: 利润表\n年份,营收\n2025,100\n\n## Sheet: 说明\n单位：万元\n\n"
    )
    assert not got.truncated and got.note is None


def test_pdf_text_comes_out_page_by_page() -> None:
    got = extract_text(_pdf("Revenue 100"), "application/pdf")
    assert got.text.startswith("## Page 1\nRevenue 100")
    assert got.note is None


def test_a_pdf_without_text_says_so() -> None:
    got = extract_text(_pdf(""), "application/pdf")
    assert got.note == NO_TEXT


def test_broken_files_are_unreadable_not_errors() -> None:
    assert extract_text(b"%PDF-1.4 broken", "application/pdf").note == UNREADABLE
    assert extract_text(b"PK\x03\x04junk", XLSX).note == UNREADABLE
    assert extract_text(b"x", "image/png").note == UNREADABLE


def test_a_workbook_that_unpacks_too_large_is_not_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(text_extract, "MAX_XLSX_UNZIPPED", 10)
    assert extract_text(_xlsx(), XLSX).note == UNREADABLE


def test_text_is_cut_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_extract, "MAX_TEXT_CHARS", 10)
    got = extract_text(("一" * 50).encode(), "text/plain; charset=utf-8")
    assert got.text == "一" * 10 and got.truncated and got.note


async def test_the_same_content_is_extracted_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake(data: bytes, content_type: str) -> Extracted:
        calls.append(content_type)
        return Extracted(text=data.decode())

    monkeypatch.setattr(text_extract, "extract_text", fake)
    extractor = CachedTextExtractor()
    sha = hashlib.sha256(b"abc").hexdigest()
    got = await asyncio.gather(
        *[
            extractor.extract(data=b"abc", content_type="text/plain", sha256=sha)
            for _ in range(3)
        ]
    )
    assert [g.text for g in got] == ["abc"] * 3 and calls == ["text/plain"]


# ---------- 契约文件 ----------


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_schema_files_are_generated_from_the_models(name: str) -> None:
    if not CONTRACT_DIR.exists():
        pytest.skip("parent contracts/ is not checked out")
    assert (CONTRACT_DIR / name).read_text(encoding="utf-8") == render(name), (
        "regenerate: python -m app.domain.knowbots.data_schema"
    )


def test_contract_manifest_digests_match_the_schemas() -> None:
    if not CONTRACT_DIR.exists():
        pytest.skip("parent contracts/ is not checked out")
    release = json.loads((CONTRACT_DIR / "contract-manifest.json").read_text())
    assert (release["contract"], release["major_version"]) == ("knowbot-data", 1)
    assert set(release["schemas"]) == set(DOCUMENTS)
    for name, digest in release["schemas"].items():
        assert hashlib.sha256((CONTRACT_DIR / name).read_bytes()).hexdigest() == digest


# ---------- 内部接口 ----------


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def test_without_the_relation_configured_everything_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in list(os.environ):
        if key.startswith("SERVICE_AUTH_READ"):
            monkeypatch.delenv(key)
    body = {
        "contract": "knowbot-data/v1",
        "purpose": {"kind": "test", "run_id": "knowlab-run-0002"},
    }
    async with _client() as h:
        assert (await h.post(f"{INTERNAL}/list", json=body)).status_code == 401
        refused = await h.post(
            f"{INTERNAL}/list", json=body, headers={"Authorization": "Bearer x"}
        )
    assert refused.status_code == 503
    assert refused.json()["code"] == "service_relation_unconfigured"


async def test_the_runtime_lists_and_reads_through_the_internal_entry(
    world: World,
) -> None:
    app.dependency_overrides[require_read_service] = lambda: ServiceCaller(
        caller="knowbench",
        principal=None,  # type: ignore[arg-type]
    )
    app.dependency_overrides[knowbot_data_routes.reader] = lambda: world.reader
    try:
        purpose = world.knowbot()
        async with _client() as h:
            listed = await h.post(
                f"{INTERNAL}/list",
                json={"contract": "knowbot-data/v1", "purpose": purpose},
            )
            read = await h.post(
                f"{INTERNAL}/read",
                json={
                    "contract": "knowbot-data/v1",
                    "purpose": purpose,
                    "dataset_id": str(world.bound.id),
                },
            )
            other = await h.post(
                f"{INTERNAL}/read",
                json={
                    "contract": "knowbot-data/v1",
                    "purpose": purpose,
                    "dataset_id": str(world.loose.id),
                },
            )
            bad = await h.post(f"{INTERNAL}/list", json={"purpose": purpose})
    finally:
        app.dependency_overrides.clear()
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert [d["name"] for d in listed.json()["datasets"]] == ["年报.csv"]
    assert read.json()["text"] == "公司,营收\nA,100\n"
    assert other.status_code == 404 and other.json()["code"] == "knowbot_data_not_found"
    assert bad.status_code == 422
