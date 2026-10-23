"""用户上传的数据（SDD 0027 决定 4）。

类型、大小、配额、只能删没被用着的、绑定只能指向本人的。
"""

from __future__ import annotations

import hashlib
import io

import botocore.session
import pytest
from botocore.stub import Stubber

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots import datasets
from app.domain.knowbots.datasets import Rejected
from app.infrastructure.storage.object_storage import ObjectStorage, StorageUnavailable
from core.config import Settings
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryDataStore,
    MemoryKnowlab,
    accepted_request,
    manifest,
)

PDF = b"%PDF-1.7\n..."


@pytest.mark.parametrize(
    ("name", "data", "want"),
    [
        ("年报.pdf", PDF, "application/pdf"),
        ("表.xlsx", b"PK\x03\x04rest", datasets.XLSX),
        (
            "老表.XLS",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest",
            "application/vnd.ms-excel",
        ),
        ("数.csv", "代码,毛利率\n600519,0.91\n".encode(), "text/csv; charset=utf-8"),
        ("说明.md", "# 口径".encode(), "text/markdown; charset=utf-8"),
    ],
)
def test_allowed_types_are_recognised_by_extension_and_content(name, data, want):
    assert datasets.content_type_of(name, data) == want


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("a.exe", b"MZ", "dataset_type_not_allowed"),
        ("noext", b"x", "dataset_type_not_allowed"),
        ("假.pdf", b"PK\x03\x04", "dataset_type_mismatch"),
        ("gbk.txt", "中文".encode("gbk"), "dataset_text_not_utf8"),
    ],
)
def test_other_types_are_refused(name, data, code):
    with pytest.raises(Rejected) as raised:
        datasets.content_type_of(name, data)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "bad", ["", " ", "a/b.pdf", "a\\b.pdf", "x\x00.pdf", "..", "n" * 201]
)
def test_names_cannot_be_paths(bad):
    with pytest.raises(Rejected):
        datasets.clean_name(bad)


def test_size_and_quota():
    datasets.check_size(1, 0)
    for size, used, code in [
        (0, 0, "dataset_empty"),
        (datasets.MAX_FILE_BYTES + 1, 0, "dataset_too_large"),
        (10, datasets.QUOTA_BYTES - 5, "dataset_quota_exceeded"),
    ]:
        with pytest.raises(Rejected) as raised:
            datasets.check_size(size, used)
        assert raised.value.code == code


def test_object_keys_are_per_owner_and_do_not_carry_the_raw_subject():
    key = datasets.object_key("user/123", "d1")
    assert key.startswith("users/") and key.endswith("/d1") and "user/123" not in key
    assert datasets.object_key("user/124", "d1") != key


def test_bindings_must_point_at_the_owners_data():
    owned = {"d1": ("1", "a" * 64)}
    good = [{"dataset_id": "d1", "version": "1", "sha256": "a" * 64}]
    assert datasets.binding_problems(good, owned) == []
    assert datasets.binding_problems([], owned) == []
    assert datasets.binding_problems(
        [{"dataset_id": "d2", "version": "1", "sha256": "a" * 64}], owned
    ) == ["a data binding is not the owner's data"]
    assert datasets.binding_problems(
        [{"dataset_id": "d1", "version": "1", "sha256": "b" * 64}], owned
    ) == ["a data binding differs from the owner's data"]
    assert datasets.binding_problems("x", owned)


@pytest.fixture
def parts():
    repo, store = MemoryKnowlab(), MemoryDataStore()
    return KnowbotDataService(repo, store), repo, store


async def test_upload_list_and_delete(parts):
    data, repo, store = parts
    found = await data.upload(OWNER, "年报.pdf", PDF)
    assert found.sha256 == hashlib.sha256(PDF).hexdigest()
    assert found.version == "1" and found.size_bytes == len(PDF)
    assert store.objects[found.object_key] == PDF
    mine, used = await data.mine(OWNER)
    assert [d.id for d in mine] == [found.id] and used == len(PDF)
    assert (await data.mine("someone-else"))[0] == []
    with pytest.raises(NotFoundError):
        await data.delete("someone-else", str(found.id))
    await data.delete(OWNER, str(found.id))
    assert store.deleted == [found.object_key]
    assert (await data.mine(OWNER))[0] == []
    with pytest.raises(NotFoundError):
        await data.delete(OWNER, str(found.id))


async def test_upload_refusals(parts, monkeypatch):
    data, _, store = parts
    with pytest.raises(BadRequestError) as raised:
        await data.upload(OWNER, "a.exe", b"MZ")
    assert raised.value.code == "dataset_type_not_allowed"
    monkeypatch.setattr(datasets, "QUOTA_BYTES", len(PDF) + 1)
    await data.upload(OWNER, "a.pdf", PDF)
    with pytest.raises(BadRequestError) as raised:
        await data.upload(OWNER, "b.pdf", PDF)
    assert raised.value.code == "dataset_quota_exceeded"
    assert len(store.objects) == 1  # 超配额的没有存进去
    store.enabled = False
    with pytest.raises(ServiceUnavailableError) as raised:
        await data.upload(OWNER, "c.pdf", PDF)
    assert raised.value.code == "storage_unavailable"


async def test_bound_data_cannot_be_deleted_and_drafts_only_bind_the_owners_data(parts):
    data, repo, _ = parts
    mine = await data.upload(OWNER, "年报.pdf", PDF)
    theirs = await data.upload("someone-else", "别人的.pdf", PDF)
    creation = KnowbotCreationService(repo, FakeRuntime())
    request = await accepted_request(creation)
    binding = lambda d: [{"dataset_id": str(d.id), "version": "1", "sha256": d.sha256}]  # noqa: E731
    with pytest.raises(BadRequestError) as raised:
        await creation.add_draft(
            str(request.id), manifest(data_bindings=binding(theirs))
        )
    assert raised.value.code == "manifest_invalid"
    await creation.add_draft(str(request.id), manifest(data_bindings=binding(mine)))
    with pytest.raises(ConcurrencyConflictError) as conflict:
        await data.delete(OWNER, str(mine.id))
    assert conflict.value.code == "dataset_in_use"


def _storage(stub_setup):
    settings = Settings(storage_backend="s3", s3_bucket="knowlab-originals")
    storage = ObjectStorage(settings)
    client = botocore.session.get_session().create_client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="y",
    )
    stubber = Stubber(client)
    stub_setup(stubber)
    stubber.activate()
    storage.s3_client = lambda: client  # type: ignore[method-assign]
    return storage, stubber


def test_put_bytes_keeps_the_returned_version_and_checks_its_digest():
    digest = hashlib.sha256(PDF).hexdigest()

    def setup(stub):
        stub.add_response("put_object", {"VersionId": "v1"})
        stub.add_response(
            "head_object",
            {
                "VersionId": "v1",
                "ContentLength": len(PDF),
                "Metadata": {"sha256": digest},
            },
        )

    storage, stubber = _storage(setup)
    stored = storage.put_bytes(
        object_key="users/x/d1", data=PDF, content_type="application/pdf"
    )
    assert (stored.version_id, stored.sha256, stored.bucket) == (
        "v1",
        digest,
        "knowlab-originals",
    )
    stubber.assert_no_pending_responses()


def test_unversioned_or_mismatched_writes_are_refused():
    storage, _ = _storage(lambda stub: stub.add_response("put_object", {}))
    with pytest.raises(RuntimeError, match="s3_version_required"):
        storage.put_bytes(object_key="k", data=PDF, content_type="application/pdf")

    def setup(stub):
        stub.add_response("put_object", {"VersionId": "v1"})
        stub.add_response(
            "head_object", {"VersionId": "v1", "ContentLength": 1, "Metadata": {}}
        )

    storage, _ = _storage(setup)
    with pytest.raises(RuntimeError, match="s3_version_metadata_mismatch"):
        storage.put_bytes(object_key="k", data=PDF, content_type="application/pdf")


def test_reads_are_checked_against_the_recorded_digest():
    def setup(stub):
        stub.add_response(
            "get_object",
            {"ContentLength": len(PDF), "Body": io.BytesIO(PDF)},
            {"Bucket": "knowlab-originals", "Key": "k", "VersionId": "v1"},
        )

    storage, _ = _storage(setup)
    with pytest.raises(RuntimeError, match="digest_mismatch"):
        storage.get_bytes(
            object_key="k", version_id="v1", expected_sha256="0" * 64, max_bytes=100
        )


def test_storage_is_off_unless_s3_is_configured():
    with pytest.raises(StorageUnavailable):
        ObjectStorage(Settings(storage_backend="disabled")).s3_client()
