"""用户上传的数据（SDD 0027 决定 4）。

只放本应用自己的对象存储桶，按用户分前缀；单文件不超过 50 MB、每人不超过 1 GB；先收
PDF、Excel、CSV、文本。数据只用于他自己的分身与测试；被已交付或在做的版本引用的，不能删。
每次上传是一份新数据，版本固定为 "1"：分身清单的 data_bindings 记「数据 id + 版本 +
sha256」，内容一变就是另一份数据，已交付的版本永远指向它测过的那一份。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

MAX_FILE_BYTES = 50 * 1024 * 1024
QUOTA_BYTES = 1024 * 1024 * 1024
MAX_DATASETS = 1000
DATASET_VERSION = "1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# 扩展名 → （内容类型，文件头）。文本类没有文件头，要求是 UTF-8
KINDS: dict[str, tuple[str, bytes | None]] = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".xlsx": (XLSX, b"PK\x03\x04"),
    ".xls": ("application/vnd.ms-excel", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    ".csv": ("text/csv; charset=utf-8", None),
    ".txt": ("text/plain; charset=utf-8", None),
    ".md": ("text/markdown; charset=utf-8", None),
}
_BAD_NAME = re.compile(r"[\x00-\x1f\x7f/\\]")


@dataclass(frozen=True)
class Rejected(Exception):
    code: str
    message: str


def clean_name(filename: str | None) -> str:
    name = (filename or "").strip()
    if not name or len(name) > 200 or _BAD_NAME.search(name) or name in {".", ".."}:
        raise Rejected("dataset_name_invalid", "The file name is not allowed")
    return name


def content_type_of(name: str, data: bytes) -> str:
    """按扩展名与文件头认类型；认不出或对不上就不收。"""
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    if suffix not in KINDS:
        raise Rejected(
            "dataset_type_not_allowed", "Only PDF, Excel, CSV and text files are taken"
        )
    content_type, magic = KINDS[suffix]
    if magic is not None:
        if not data.startswith(magic):
            raise Rejected("dataset_type_mismatch", "The file does not match its type")
    else:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise Rejected(
                "dataset_text_not_utf8", "Text files must be UTF-8"
            ) from None
    return content_type


def check_size(size: int, used: int) -> None:
    if size == 0:
        raise Rejected("dataset_empty", "The file is empty")
    if size > MAX_FILE_BYTES:
        raise Rejected("dataset_too_large", "A file can be at most 50 MB")
    if used + size > QUOTA_BYTES:
        raise Rejected("dataset_quota_exceeded", "Your data can be at most 1 GB")


def check_count(count: int) -> None:
    """一个人最多 1000 份。

    测试同意一条覆盖他全部的资料，knowbase 一条同意依据最多 1000 份。
    """
    if count >= MAX_DATASETS:
        raise Rejected("dataset_count_exceeded", "You can keep at most 1000 files")


def object_key(owner_subject: str, dataset_id: str) -> str:
    """按用户分前缀；前缀是所有者的摘要，不把 Casdoor id 原样放进对象名。"""
    owner = hashlib.sha256(owner_subject.encode("utf-8")).hexdigest()[:32]
    return f"users/{owner}/{dataset_id}"


def binding_problems(bindings: Any, owned: dict[str, tuple[str, str]]) -> list[str]:
    """清单的 data_bindings 只能指向这个用户自己的、没删的数据（C-L2）。

    版本与 sha256 都要对得上。owned：数据 id → （版本，sha256）。
    """
    if not isinstance(bindings, list):
        return ["data_bindings must be a list"]
    problems = []
    for item in bindings:
        if not isinstance(item, dict):
            problems.append("each data binding must be an object")
            continue
        found = owned.get(str(item.get("dataset_id")))
        if found is None:
            problems.append("a data binding is not the owner's data")
        elif (str(item.get("version")), str(item.get("sha256"))) != found:
            problems.append("a data binding differs from the owner's data")
    return problems
