"""知识库栏与工作稿里挑数据（SDD 0046 第六节；专家孵化方案第 7.1、8.2 节）。

- 知识库栏（web端右边「知识库」）：凭作者本人令牌向 knowbase 取作者展示给 knowlab 的
  资料，照原样列（名字、描述、版本、大小）；在 knowbase 管，knowlab 不改。knowlab 自己
  不存这份列表。
- 挑数据：作者在工作稿的「数据」一栏从这批里挑几份。挑的时候核每一份都在 knowlab 栏里，
  凭本人令牌取它的格式（名字、文件类型、描述的几维、已就绪的读法），连同挑的编号一起记进
  工作稿（data_picks 与 data_formats）。AI 起草、按指令改的每一轮把格式交给运行方，不给
  内容（0029 第 6.4 节）；平台人员发起的那一轮没有作者令牌，用的也是记下的这份。交付时
  挑的数据再冻结成绑定（0046 第五节，交付那一块做）。
- 只有作者本人；不是自己的名称与没有的一样，都是 404。挑的编号不算作者的判断文字，不要
  出处（workdraft.py 文件头）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from app.application.errors.exceptions import BadGatewayError, BadRequestError
from app.application.ports.knowbots import (
    KnowbaseLibraryClient,
    KnowlabRepository,
    WorkdraftRecord,
)
from app.application.services.knowbot_workdraft import (
    KnowbotWorkdraftService,
    parse_body,
)
from app.domain.knowbots.incubation import DataFormat
from app.domain.knowbots.workdraft import apply

UserToken = Callable[[], Awaitable[str]]
MAX_PICKS = 50  # 一份工作稿最多挑几份（契约 data_formats 上限 200，留余量）
DESCRIBED = (
    "subject",
    "doc_type",
    "period_start",
    "period_end",
    "data_format",
    "language",
)


def _text(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def shelf_item(entry: dict[str, Any]) -> dict[str, Any]:
    """栏里的一份，页面上要的几项：名字、是不是自己的、分类、来源、许可档、描述、
    这一版的编号、类型、大小。"""
    material = entry.get("material") or {}
    version = entry.get("version") or {}
    described = material.get("description") or {}
    return {
        "material_id": str(material.get("material_id") or ""),
        "name": str(material.get("name") or ""),
        "mine": bool(material.get("mine")),
        "category": entry.get("category"),
        "content_origin": material.get("content_origin"),
        "license": material.get("license"),
        "description": {
            **{key: described.get(key) for key in DESCRIBED},
            "status": described.get("status"),
        },
        "version_no": version.get("version_no"),
        "sha256": version.get("sha256"),
        "media_type": version.get("media_type"),
        "size_bytes": version.get("size_bytes"),
        "created_at": material.get("created_at"),
    }


def format_of(
    item: dict[str, Any],
    readings: Sequence[dict[str, Any]],
    tables: Sequence[dict[str, Any]],
) -> DataFormat:
    """描述、读法与 knowbase 交回的表轴；不下载或解析原件。"""
    described = item.get("description") or {}
    ready = sorted(
        {
            str(r.get("kind"))
            for r in readings
            if r.get("status") == "ready" and isinstance(r.get("kind"), str)
        }
    )
    return DataFormat.model_validate(
        {
            "name": (item.get("name") or item["material_id"])[:255],
            "media_type": _text(item.get("media_type"), 128) or "",
            "description": {
                "subject": _text(described.get("subject"), 64),
                "doc_type": _text(described.get("doc_type"), 64),
                "period_start": _text(described.get("period_start"), 10),
                "period_end": _text(described.get("period_end"), 10),
                "data_format": _text(described.get("data_format"), 64),
                "language": _text(described.get("language"), 16),
            },
            "readings": ready[:10],
            "tables": tables,
        }
    )


class KnowbotLibraryService:
    def __init__(
        self,
        repo: KnowlabRepository,
        knowbase: KnowbaseLibraryClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.knowbase = knowbase
        self.workdrafts = KnowbotWorkdraftService(repo, now=now)

    async def shown(self, user_token: UserToken) -> list[dict[str, Any]]:
        """知识库栏：作者展示给 knowlab 的资料（knowbase 说了算，照它的次序）。"""
        return [
            shelf_item(e)
            for e in await self.knowbase.column(await user_token())
            if (e.get("material") or {}).get("material_id")
        ]

    async def pick(
        self,
        owner_subject: str,
        request_id: str,
        *,
        revision: int,
        picks: Sequence[str],
        user_token: UserToken,
    ) -> WorkdraftRecord:
        """挑数据：换成这几份（整份换，给空的就是都不要了）。每一份都要在知识库栏里；
        格式当场取下记着。带 revision，对不上 409（同工作稿）。"""
        wanted = list(dict.fromkeys(picks))
        if len(wanted) != len(picks) or len(wanted) > MAX_PICKS:
            raise BadRequestError(
                f"Pick at most {MAX_PICKS} different materials", code="picks_invalid"
            )
        request = await self.workdrafts._request(owner_subject, request_id)
        formats: dict[str, Any] = {}
        if wanted:
            token = await user_token()
            shown = {
                item["material_id"]: item
                for item in (shelf_item(e) for e in await self.knowbase.column(token))
            }
            missing = [p for p in wanted if p not in shown]
            if missing:
                raise BadRequestError(
                    "Only materials shown to knowlab can be picked: "
                    + ", ".join(m[:64] for m in missing[:5]),
                    code="pick_not_shown",
                )
            for material_id in wanted:
                item = shown[material_id]
                version = item.get("version_no")
                if type(version) is not int or version < 1:
                    raise BadGatewayError(
                        "knowbase omitted the version", code="knowbase_contract_invalid"
                    )
                shape = await self.knowbase.read_format(token, material_id, version)
                if shape is None:
                    # knowbase 读不出这一份的表轴：按「表轴未知」带过去（账 186）
                    shape = {**item, "tables": []}
                if any(
                    shape.get(key) != item.get(key)
                    for key in ("material_id", "version_no", "sha256", "media_type")
                ):
                    raise BadGatewayError(
                        "knowbase format does not match the selected version",
                        code="knowbase_contract_invalid",
                    )
                readings = await self.knowbase.renditions(token, material_id, version)
                formats[material_id] = format_of(
                    item, readings, shape["tables"]
                ).model_dump(mode="json")
        async with self.repo.transaction():
            current = await self.workdrafts.locked(request, revision)
            body = parse_body(apply(current.body, "data_picks", wanted))
            doc = body.model_dump(mode="json")
            await self.repo.update_workdraft(
                current.id,
                body=doc,
                sources=dict(current.sources),
                revision=current.revision + 1,
                data_formats=formats,
            )
        found = await self.repo.get_workdraft(request.id)
        assert found is not None
        return found
