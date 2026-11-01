"""交付（SDD 0046 第五节；0043 第 4.2、4.3、4.4 节；knowmart 0032 待定第一项）。

作者一侧（web端）：
- 「提交交付」：选这一版、定自用或公开、授权审核员看这次交付。这一下就是用户同意门
  （平台孵化、平台测的那一版要平台先交回才看得到，先用户、后运营照旧）。提交时：
  1. 把名下专家推进 knowbase 名录、冻结作者绑给这位专家的数据（`expert:<专家>@<版本>`，
     0044 第三节）；自用转公开沿用上一次冻结的那几份（同一版，买家那一版不变）；
  2. 凭 knowbase 预查所绑数据的许可：自用要每份还在，公开要每份都能经专家给别人用
     （0043 第 4.3 节；knowmart 收清单时还会硬查一次）；
  3. 凭作者本人令牌在 knowbase 留下 `platform:knowlab:review:<交付过程>`（审核员看这次
     交付的绑定）；公开且自助测的另留 `platform:knowlab:rerun:<交付过程>`（平台重跑要读
     的冻结输入，0043 第 2.7 节「自助重跑」）；
  4. 自用的轻审：能自动查的自动查（domain/knowbots/delivery.auto_check），都过就算
     审过、马上交 knowmart；查不过才交平台人员看。
- 自用转公开：上架了的自用版本再提交一次公开，重查许可，走完整审核（自助测的要重跑）。

平台一侧（管理端）：
- 公开且自助测的：平台发起重跑（在作者的账号下、作者的电脑、作者的 key，decisions
  ⑯），审核员看重跑的结论，不看原始数据。
- 审核：过（公开的选类目）/ 不过与理由。过的再预查一次许可，交 knowmart（带类目、
  清单 v2 带冻结的绑定）；交不成记下、可重试。审完（过或不过）撤销给审核员与重跑的
  依据；撤不成由后台补撤。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.application.errors.exceptions import (
    AppException,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import (
    DeliveryRecord,
    ExpertRecord,
    KnowbaseDeliveryClient,
    KnowlabRepository,
    KnowmartClient,
    RequestRecord,
    TestRecord,
)
from app.application.services.knowbot_experts import KnowbotExpertService, stage_of
from app.application.services.knowbot_service import KnowbotCreationService, parse_id
from app.domain.knowbots.delivery import (
    AUTO_REVIEWER,
    DeliveryReviewIn,
    DeliverySubmitIn,
    auto_check,
    binding_problem,
)
from app.domain.knowbots.lifecycle import draft_label

log = logging.getLogger(__name__)
UserToken = Callable[[], Awaitable[str]]
# 清单 v2 的 FrozenBinding 收的几样（knowmart 的外层不收别的键）
BINDING_KEYS = (
    "material_id",
    "version_no",
    "sha256",
    "rendition_kind",
    "rendition_no",
    "rendition_sha256",
    "basis_ref",
)
RERUN_TIMEOUT_SECONDS = 3600
MAX_RERUNS = 4  # 第一次 + 平台出错后再发起最多 3 次（决定 10-30）
WITHDRAWN_IN_KNOWBASE = "作者在 knowbase 撤了审核授权"
DIRECTORY_BATCH = 200


def _conflict(message: str, code: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code=code)


def spent_of(test: TestRecord) -> Decimal:
    """一次重跑花了多少：运行方报了最终 cost.spent 的按实算；超时重置、被停下或没报
    花费的，不知道实际花了多少，按当次预算全额计入。"""
    budget = Decimal(str(test.budget_limit or "0"))
    if str(test.error or "").startswith(("timed out", "stopped:")):
        return budget
    cost = (test.report or {}).get("cost")
    raw = cost.get("spent") if isinstance(cost, Mapping) else None
    if raw is None:
        return budget
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return budget


def is_open(delivery: DeliveryRecord) -> bool:
    """还没走完的：在审的，或审过了、还没交成 knowmart 的。"""
    return delivery.status == "reviewing" or (
        delivery.status == "approved" and delivery.delivered_at is None
    )


def manifest_with_bindings(
    manifest: dict[str, Any], bindings: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """交出去的是清单 v2：草稿的清单加上交付时冻结的绑定（数据留在 knowbase）。"""
    if not bindings:
        return manifest
    return {
        **manifest,
        "bindings": [
            {k: b[k] for k in BINDING_KEYS if b.get(k) is not None} for b in bindings
        ],
    }


class KnowbotDeliveryService:
    def __init__(
        self,
        repo: KnowlabRepository,
        knowmart: KnowmartClient,
        knowbase: KnowbaseDeliveryClient | None,
        creation: KnowbotCreationService,
        experts: KnowbotExpertService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.knowmart = knowmart
        self.knowbase = knowbase
        self.creation = creation
        self.experts = experts
        self.now = now

    def _kb(self) -> KnowbaseDeliveryClient:
        if self.knowbase is None:
            raise ServiceUnavailableError(
                "knowbase is not configured", code="knowbase_unconfigured"
            )
        return self.knowbase

    # ---------- 作者 ----------
    async def submit(
        self,
        owner_subject: str,
        expert_id: str,
        body: DeliverySubmitIn,
        user_token: UserToken,
    ) -> DeliveryRecord:
        """「提交交付」（SDD 0046 第五节）。

        先预查（knowbase 回「现在冻结会冻成什么」，不写）、自动查、算重跑要读的输入，
        都不会留下什么；过了才冻结、留依据、建交付。从冻结起整段包住：中途出错，已留的
        依据按这次的编号撤掉，不留孤立的 `platform:knowlab:review:<编号>`。
        """
        expert, request = await self.experts.my_expert(owner_subject, expert_id)
        earlier = await self.repo.list_deliveries(expert_id=expert.id)
        if any(is_open(d) for d in earlier):
            raise _conflict("This expert is already being delivered", "delivery_open")
        stage = stage_of(expert)
        conversion = (
            stage == "listed"
            and expert.review_listing == "self"
            and body.listing == "public"
        )
        if stage != "awaiting_user" and not conversion:
            raise _conflict("This expert cannot be delivered now", "expert_state")
        test = (
            None if expert.test_id is None else await self.repo.get_test(expert.test_id)
        )
        if test is None:
            raise _conflict("This expert has no passed test", "expert_state")
        test_mode = "platform" if test.mode == "platform" else "self"
        needs_rerun = body.listing == "public" and test_mode == "self"
        if needs_rerun and body.budget_limit is None:
            raise BadRequestError(
                "Set the budget for the platform rerun", code="budget_required"
            )
        knowbase = self._kb()
        public = body.listing == "public"
        if conversion:
            # 同一版：沿用上架那一次冻结的（买家那一版不变；改绑定要出新一版）。冻结时
            # 只许本人用的，现在改高许可也不够给别人用（签授权看冻结的那一档）
            bindings = next(
                (
                    list(d.bindings)
                    for d in reversed(earlier)
                    if d.status == "approved" and d.delivered_at is not None
                ),
                [],
            )
            check = await self._check(owner_subject, bindings, public, frozen=True)
        else:
            await self.push_directory(request)
            bindings = await knowbase.freeze(
                str(request.id), str(expert.version), author=owner_subject, preview=True
            )
            check = await self._check(owner_subject, bindings, public, frozen=False)
        if check["problem"] is not None and not (
            conversion
            and body.raise_frozen
            and check["problem"] == "binding_frozen_self_only"
        ):
            raise _conflict(
                "The bound data cannot go with this listing", str(check["problem"])
            )
        rerun_items = await self._rerun_inputs(request, test) if needs_rerun else []
        record = DeliveryRecord(
            id=uuid.uuid4(),
            expert_id=expert.id,
            request_id=request.id,
            listing=body.listing,
            conversion=conversion,
            test_mode=test_mode,
            needs_rerun=needs_rerun,
            budget_limit=body.budget_limit if needs_rerun else None,
            submitted_by=owner_subject,
            submitted_at=self.now(),
            bindings=bindings,
            binding_check=check,
            auto_check=await self._auto_check(expert, test),
            consents={"authorized_at": self.now().isoformat()},
        )
        did_freeze = False
        try:
            if not conversion:
                # 冻结只一次：以前冻结过的回那时的版本与许可，照冻结的再核一遍
                frozen = await knowbase.freeze(
                    str(request.id), str(expert.version), author=owner_subject
                )
                did_freeze = True
                check = await self._check(owner_subject, frozen, public, frozen=True)
                record = replace(record, bindings=frozen, binding_check=check)
                if check["problem"] is not None:
                    raise _conflict(
                        "The bound data cannot go with this listing",
                        str(check["problem"]),
                    )
            token = await user_token()
            review_items = [
                {"material_id": b["material_id"], "version_no": b["version_no"]}
                for b in record.bindings
            ]
            # 不绑数据也留一条（账号级）：作者在 knowbase 撤了这次审核授权查得到；
            # 只读公开原件、引用的重跑也凭这次过程的同意签机器授权
            record.consents["review"] = await knowbase.consent_platform(
                token, kind="review", ref=str(record.id), items=review_items
            )
            if needs_rerun:
                record.consents["rerun"] = await knowbase.consent_platform(
                    token, kind="rerun", ref=str(record.id), items=rerun_items
                )
            if (
                conversion
                and body.raise_frozen
                and check["problem"] == "binding_frozen_self_only"
            ):
                # 作者勾了「我有权」：授权留好之后，把冻结时只许本人用的那几份往高改
                # （决定 10-30；上面的预查已核过每份现在的许可都够，清单不变）
                for item in check["items"]:
                    if item.get("frozen_license") in (None, "via_expert", "sale"):
                        continue
                    await knowbase.raise_frozen_license(
                        str(request.id),
                        str(expert.version),
                        str(item["material_id"]),
                        author=owner_subject,
                        license_="via_expert",
                    )
                bindings = [
                    {**b, "license": "via_expert"}
                    if b.get("license") not in ("via_expert", "sale")
                    else b
                    for b in record.bindings
                ]
                check = await self._check(owner_subject, bindings, public, frozen=True)
                record = replace(record, bindings=bindings, binding_check=check)
                if check["problem"] is not None:
                    raise _conflict(
                        "The bound data cannot go with this listing",
                        str(check["problem"]),
                    )
            async with self.repo.transaction():
                current = await self.repo.get_expert(expert.id, for_update=True)
                assert current is not None
                if stage_of(current) != stage or any(
                    is_open(d)
                    for d in await self.repo.list_deliveries(expert_id=expert.id)
                ):
                    raise _conflict("This expert changed meanwhile", "expert_state")
                await self.repo.create_delivery(record)
                if not conversion:
                    await self.repo.update_expert(
                        expert.id,
                        user_accepted=True,
                        user_decided_by=owner_subject,
                        user_decided_at=self.now(),
                    )
        except Exception:
            ended = bool(
                await self._end_consents(
                    record, owner_subject, "cancelled", always=True
                )
            )
            voided = (
                await self._void_frozen_if_unused(
                    expert.id, request_id=request.id, owner_subject=owner_subject
                )
                if did_freeze
                else True
            )
            if not ended or not voided:
                await self._leave_for_close(
                    record,
                    owner_subject,
                    unended_consents=not ended,
                    pending_void=not voided,
                )
            raise

        if body.listing == "self" and (record.auto_check or {}).get("passed"):
            return await self._decide(
                record.id, approved=True, reason="", category=None, by=AUTO_REVIEWER
            )
        return await self._reload(record.id)

    async def withdraw(self, owner_subject: str, expert_id: str) -> DeliveryRecord:
        """作者撤回没走完的交付（在审的，或审过了还没交成的）：停在跑的重跑、撤给审核员
        与重跑的依据；第一次交付的专家回到「等你提交」。"""
        expert, _ = await self.experts.my_expert(owner_subject, expert_id)
        found = next(
            (
                d
                for d in await self.repo.list_deliveries(expert_id=expert.id)
                if is_open(d)
            ),
            None,
        )
        if found is None:
            raise _conflict("Nothing is being delivered", "delivery_state")
        await self._not_in_knowmart(found)
        return await self._withdraw(found, by=owner_subject, reason="作者撤回")

    async def void(
        self, delivery_id: str, operator: str, reason: str
    ) -> DeliveryRecord:
        """平台人员作废审过了却一直交不成的那一次（如审后作者改低了许可，knowmart 一直
        拒）：撤依据，第一次交付的专家回到「等作者提交」，不再挡这一版。"""
        found = await self.delivery(delivery_id)
        if found.status != "approved" or found.delivered_at is not None:
            raise _conflict("Only an undelivered approval is voided", "delivery_state")
        await self._not_in_knowmart(found)
        return await self._withdraw(found, by=operator, reason=reason.strip())

    async def _not_in_knowmart(self, found: DeliveryRecord) -> None:
        """审过了还没记交成的：先问 knowmart 收没收下（可能只是回执丢了）。收下了的不许
        撤回、作废，回 409 说明——请重试交付（同一单号重放，把交成补记上），要不用了
        就上架后停用。"""
        if found.status != "approved":
            return
        request = await self.repo.get_request(found.request_id)
        expert = await self.repo.get_expert(found.expert_id)
        assert request is not None and expert is not None
        versions = await self.knowmart.versions(
            owner_subject=request.owner_subject, knowbot_ref=f"knowlab-{request.id}"
        )
        mine = next(
            (v for v in versions if str(v.get("version")) == str(expert.version)), None
        )
        received = (
            mine is not None
            and mine.get("status") != "stopped"
            and (not found.conversion or mine.get("public_allowed") is True)
        )
        if received:
            raise _conflict(
                "knowmart already took this delivery; retry it to record it",
                "delivered_in_knowmart",
            )

    async def my_deliveries(
        self, owner_subject: str, expert_id: str
    ) -> list[DeliveryRecord]:
        expert, _ = await self.experts.my_expert(owner_subject, expert_id)
        return list(await self.repo.list_deliveries(expert_id=expert.id))

    # ---------- 平台 ----------
    async def delivery(self, delivery_id: str) -> DeliveryRecord:
        found = await self.repo.get_delivery(parse_id(delivery_id, "delivery"))
        if found is None:
            raise NotFoundError("No such delivery", code="delivery_not_found")
        return found

    async def start_rerun(self, delivery_id: str, operator: str) -> TestRecord:
        """平台发起重跑（公开、自助测的）：没跑过的、上一次平台出错的可以发起。"""
        found = await self.delivery(delivery_id)
        if found.status != "reviewing" or not found.needs_rerun:
            raise _conflict("This delivery needs no rerun", "rerun_not_needed")
        spent = found.rerun_spent
        if found.rerun_test_id is not None:
            # 只有上一次是平台出错的能再发起；没通过的只能审成不过（重跑花作者的钱，
            # 不许跑到碰巧过为止）
            last = await self.repo.get_test(found.rerun_test_id)
            if last is not None and last.status != "platform_error":
                raise _conflict("The rerun is running or done", "rerun_exists")
            if last is not None:
                spent += spent_of(last)
        # 作者设的上限按一次交付累计；平台出错后再发起最多 3 次（决定 10-30）
        if found.rerun_attempts >= MAX_RERUNS:
            raise _conflict("No more reruns for this delivery", "rerun_attempts_used")
        assert found.budget_limit is not None
        left = found.budget_limit - spent
        if left <= 0:
            raise _conflict("The rerun budget is used up", "rerun_budget_used")
        test = await self.creation.start_rerun(
            found,
            starter=operator,
            timeout_seconds=RERUN_TIMEOUT_SECONDS,
            budget=left,
        )
        async with self.repo.transaction():
            current = await self.repo.get_delivery(found.id, for_update=True)
            assert current is not None
            still = current.status == "reviewing" and current.consent_ended_at is None
            if still:
                await self.repo.update_delivery(
                    found.id,
                    rerun_test_id=test.id,
                    rerun_attempts=current.rerun_attempts + 1,
                    rerun_spent=spent,
                )
        if not still:
            # 发起的同时作者撤回了（或审完了）：这次重跑立即停掉
            await self.creation.stop_rerun(test.id, "delivery no longer in review")
            raise _conflict("This delivery is no longer in review", "delivery_state")
        return test

    async def review(
        self, delivery_id: str, body: DeliveryReviewIn, reviewer: str
    ) -> DeliveryRecord:
        """平台人员审：过（公开的选类目；自助测的公开要重跑通过；再预查一次许可）即交
        knowmart；不过写理由。"""
        found = await self.delivery(delivery_id)
        if found.status != "reviewing":
            raise _conflict("Already decided", "delivery_decided")
        if body.approved:
            if found.needs_rerun:
                rerun = (
                    None
                    if found.rerun_test_id is None
                    else await self.repo.get_test(found.rerun_test_id)
                )
                if rerun is None or rerun.status != "passed":
                    raise _conflict(
                        "The platform rerun has not passed", "rerun_not_passed"
                    )
            if found.listing == "public" and body.category is None:
                raise BadRequestError(
                    "Choose a category for a public expert", code="category_required"
                )
            request = await self.repo.get_request(found.request_id)
            assert request is not None
            check = await self._check(
                request.owner_subject,
                found.bindings,
                found.listing == "public",
                frozen=True,
            )
            async with self.repo.transaction():
                await self.repo.update_delivery(found.id, binding_check=check)
            if check["problem"] is not None:
                raise _conflict(
                    "The bound data cannot go with this listing", str(check["problem"])
                )
        return await self._decide(
            found.id,
            approved=body.approved,
            reason=body.reason.strip(),
            category=body.category,
            by=reviewer,
        )

    async def retry(self, delivery_id: str) -> DeliveryRecord:
        """重试交付：审过了、上次交 knowmart 没成功的。"""
        found = await self.delivery(delivery_id)
        if found.status != "approved" or found.delivered_at is not None:
            raise _conflict("Only an approved delivery is retried", "delivery_state")
        return await self._deliver(found.id)

    async def close_pending(self, limit: int = 100) -> int:
        """后台补撤：审完或撤回后，依据、冻结作废、knowmart 补停/收回公开还没办完的。"""
        done = 0
        for found in await self.repo.deliveries_to_close(limit):
            request = await self.repo.get_request(found.request_id)
            assert request is not None
            reason = {"approved": "finished", "rejected": "rejected"}.get(
                found.status, "cancelled"
            )
            done += await self._close_delivery(
                found,
                request.owner_subject,
                reason,
                void_freeze=bool(found.consents.get("pending_void")),
            )
        return done

    async def check_consents(self, limit: int = 100) -> int:
        """后台每分钟：在审的交付，作者给审核员留的依据
        （`platform:knowlab:review:<交付>`）还在不在。作者在 knowbase 总栏撤了，就照
        作者撤回处理：停在跑的重跑、撤重跑的授权、交付记撤回（撤回人是作者），运营随之
        看不到清单与测试。knowbase 按架构不调别的应用，所以由这边来查（决定 10-30）。
        没在 knowbase 留依据的（没绑数据）不用查。回撤回了几次。"""
        if self.knowbase is None:
            return 0
        done = 0
        waiting = sorted(
            (
                d
                for d in await self.repo.list_deliveries(status="reviewing")
                if d.consent_ended_at is None and int(d.consents.get("review") or 0) > 0
            ),
            # 轮换：最久没查的先查，一轮最多 limit 个，下一轮接着查后面的
            key=lambda d: str(d.consents.get("checked_at") or ""),
        )[:limit]
        for found in waiting:
            request = await self.repo.get_request(found.request_id)
            assert request is not None
            try:
                live = await self.knowbase.platform_consent_live(
                    subject=request.owner_subject, kind="review", ref=str(found.id)
                )
            except AppException as exc:
                log.warning(
                    "delivery consent not checked delivery=%s code=%s",
                    found.id,
                    exc.code,
                )
                continue
            if live > 0:
                async with self.repo.transaction():
                    current = await self.repo.get_delivery(found.id, for_update=True)
                    assert current is not None
                    await self.repo.update_delivery(
                        found.id,
                        consents={
                            **current.consents,
                            "checked_at": self.now().isoformat(),
                        },
                    )
                continue
            try:
                withdrawn = await self._withdraw(
                    found,
                    by=request.owner_subject,
                    reason=WITHDRAWN_IN_KNOWBASE,
                    background=True,
                )
            except AppException as exc:
                log.warning(
                    "delivery not withdrawn delivery=%s code=%s", found.id, exc.code
                )
                continue
            done += withdrawn.status == "withdrawn"
        return done

    # ---------- 名录（0044 第三节；0043 第 4.4 节） ----------
    async def push_directory(self, request: RequestRecord) -> None:
        """把名下这位专家推进 knowbase 名录（作者在总栏「绑给专家」只认推来的）。"""
        await self._push(request, await self._directory_entry(request))

    async def sync_directory(self) -> int:
        """后台每分钟：有准专家、名称或最新版本变了的推一次。

        一次查询只取名称与最新准专家的版本号（不读创意、清单的大栏位），只推变了的；
        推不成的记次数与下次再试的时候（按次数往后推），也算进每轮上限；knowbase 不可达
        就这一轮停下，不对每个名称都超时一遍。没出过准专家的不推（名录是专家的）。"""
        if self.knowbase is None:
            return 0
        now = self.now()
        tried = done = 0
        for row in await self.repo.directory_rows():
            wanted = {
                "name": row["title"],
                "latest_version": draft_label(row["version"], row["revision"]),
            }
            sent = row["directory_sent"] or {}
            if {k: sent.get(k) for k in wanted} == wanted:
                continue
            if str(sent.get("retry_after") or "") > now.isoformat():
                continue
            if tried >= DIRECTORY_BATCH:
                break  # 一轮最多试这么多（推不成的也算），其余下一分钟再推
            tried += 1
            try:
                await self._kb().push_expert(
                    str(row["request_id"]),
                    owner=row["owner_subject"],
                    name=wanted["name"],
                    latest_version=wanted["latest_version"],
                )
            except AppException as exc:
                failures = int(sent.get("failures") or 0) + 1
                wait = timedelta(minutes=min(2**failures, 24 * 60))
                async with self.repo.transaction():
                    await self.repo.set_directory_sent(
                        row["request_id"],
                        {
                            **{k: sent.get(k) for k in wanted},
                            "failures": failures,
                            "retry_after": (now + wait).isoformat(),
                        },
                    )
                log.warning(
                    "expert directory not pushed request=%s code=%s",
                    row["request_id"],
                    exc.code,
                )
                if isinstance(exc, ServiceUnavailableError):
                    break
                continue
            async with self.repo.transaction():
                await self.repo.set_directory_sent(row["request_id"], wanted)
            done += 1
        return done

    async def _push(self, request: RequestRecord, wanted: dict[str, Any]) -> None:
        await self._kb().push_expert(
            str(request.id),
            owner=request.owner_subject,
            name=wanted["name"],
            latest_version=wanted["latest_version"],
        )
        async with self.repo.transaction():
            await self.repo.set_directory_sent(request.id, wanted)

    async def _directory_entry(self, request: RequestRecord) -> dict[str, Any]:
        drafts = await self.repo.list_drafts(request_id=request.id)
        latest = max(drafts, key=lambda d: d.revision, default=None)
        return {
            "name": request.title,
            "latest_version": None
            if latest is None
            else draft_label(latest.version, latest.revision),
        }

    # ---------- 内部 ----------
    async def _check(
        self,
        owner: str,
        bindings: Sequence[dict[str, Any]],
        public: bool,
        *,
        frozen: bool,
    ) -> dict[str, Any]:
        """所绑数据的许可预查（knowbase `/service/expert-bindings/check`）。

        frozen：这几份已冻结，带上凭的 `expert:<专家>@<版本>` 问，knowbase 再回冻结时的
        许可；公开要冻结的那一档也在「可以经专家给别人用」以上（签请专家的授权看它），
        不够的是 `binding_frozen_self_only`，要出新一版。没冻结的（预查）只看现在的。
        """
        checked_at = self.now().isoformat()
        if not bindings:
            return {"problem": None, "items": [], "checked_at": checked_at}
        states = await self._kb().binding_states(
            [
                {
                    "owner": owner,
                    "material_id": b["material_id"],
                    "version_no": int(b["version_no"]),
                }
                | (
                    {"basis_ref": b["basis_ref"]}
                    if frozen and b.get("basis_ref")
                    else {}
                )
                for b in bindings
            ]
        )
        return {
            "problem": binding_problem(states, public=public),
            "items": [
                {
                    "material_id": b["material_id"],
                    "version_no": b["version_no"],
                    "state": s.get("state"),
                    "license": s.get("license"),
                    "frozen_license": s.get("frozen_license"),
                    "shareable": s.get("shareable") is True,
                }
                for b, s in zip(bindings, states, strict=True)
            ],
            "checked_at": checked_at,
        }

    async def _auto_check(
        self, expert: ExpertRecord, test: TestRecord
    ) -> dict[str, Any]:
        draft = await self.repo.get_draft(expert.draft_id)
        assert draft is not None
        version = await self.repo.get_version(draft.request_id, draft.request_version)
        acceptance = await self.repo.get_acceptance(
            draft.request_id, draft.request_version
        )
        outputs = {
            o.case_id: o.outputs
            for o in await self.repo.list_test_outputs(test.draft_id, test.run_id)
        }
        findings = auto_check(
            report=test.report,
            test_status=test.status,
            accepted=acceptance is not None and acceptance.accepted,
            case_ids=[
                str(c.get("case_id")) for c in (version.cases if version else [])
            ],
            outputs=outputs,
        )
        return {
            "passed": not findings,
            "findings": findings,
            "checked_at": self.now().isoformat(),
        }

    async def _rerun_inputs(
        self, request: RequestRecord, test: TestRecord
    ) -> list[dict[str, Any]]:
        """重跑要读的冻结输入里作者自己展示给 knowlab 的那几份（公开原件、引用不用作者
        给平台同意）：那次自助测试确认过的测试集。"""
        found = await self.repo.get_test_set(request.id, from_test_id=test.id) or (
            await self.repo.get_test_set(request.id, version=test.test_set_version)
            if test.test_set_version is not None
            else None
        )
        if found is None:
            return []
        items: dict[tuple[str, int], None] = {}
        for case in found.cases.values():
            for item in (case or {}).get("items") or []:
                if item.get("basis", "display") == "display":
                    items[(str(item["material_id"]), int(item["version_no"]))] = None
        return [{"material_id": m, "version_no": v} for m, v in items]

    async def _decide(
        self,
        delivery_id: uuid.UUID,
        *,
        approved: bool,
        reason: str,
        category: str | None,
        by: str,
    ) -> DeliveryRecord:
        now = self.now()
        async with self.repo.transaction():
            found = await self.repo.get_delivery(delivery_id, for_update=True)
            assert found is not None
            if found.status != "reviewing":
                raise _conflict("Already decided", "delivery_decided")
            await self.repo.update_delivery(
                found.id,
                status="approved" if approved else "rejected",
                decided_by=by,
                decided_at=now,
                reason=reason or None,
                category=category,
            )
            if not found.conversion:
                await self.repo.update_expert(
                    found.expert_id,
                    review_approved=approved,
                    review_listing=found.listing if approved else None,
                    review_reason=reason or None,
                    reviewed_by=by,
                    reviewed_at=now,
                )
        if not approved:
            # 审成不过：用锁里读出的最新记录停在跑的重跑（不接着在作者电脑上花作者的钱）
            await self._stop_rerun(found, "delivery rejected")
        request = await self.repo.get_request(found.request_id)
        assert request is not None
        await self._close_delivery(
            found,
            request.owner_subject,
            "finished" if approved else "rejected",
        )
        if approved:
            try:
                return await self._deliver(found.id)
            except AppException:
                pass  # 错误已记下；审核结论照样留着，等重试
        elif not found.conversion:
            await self.experts.tell_unlisted(found.request_id)
        return await self._reload(found.id)

    async def _deliver(self, delivery_id: uuid.UUID) -> DeliveryRecord:
        found = await self._reload(delivery_id)
        expert = await self.repo.get_expert(found.expert_id)
        request = await self.repo.get_request(found.request_id)
        assert expert is not None and request is not None
        draft = await self.repo.get_draft(expert.draft_id)
        assert draft is not None
        try:
            receipt = await self.knowmart.deliver(
                # 单号就是这次交付：撤回后再交是新的一次，knowmart 不会把旧的当重放
                delivery_id=f"knowlab-delivery-{found.id}",
                owner_subject=request.owner_subject,
                knowbot_ref=f"knowlab-{request.id}",
                manifest=manifest_with_bindings(draft.manifest, found.bindings),
                request_public=found.listing == "public",
                category=found.category,
            )
        except AppException as exc:
            error = f"delivery failed: {exc.code}"
            async with self.repo.transaction():
                await self.repo.update_delivery(found.id, delivery_error=error)
                if not found.conversion:
                    await self.repo.update_expert(expert.id, delivery_error=error)
            raise
        now = self.now()
        async with self.repo.transaction():
            locked = await self.repo.get_delivery(found.id, for_update=True)
            assert locked is not None
            landed = locked.status == "approved"
            if landed:
                await self.repo.update_delivery(
                    found.id, delivered_at=now, receipt=receipt, delivery_error=None
                )
            current = await self.repo.get_expert(expert.id, for_update=True)
            assert current is not None
            if not landed:
                if not found.conversion and current.stopped_at is None:
                    # 首次交付与撤回撞上：knowmart 已建这一版且即将停用（版本号不可改）
                    # 不能放回「等作者提交」，直接标停用、写明原因
                    await self.repo.update_expert(
                        expert.id,
                        knowmart_knowbot_id=str(receipt["knowbot_id"]),
                        digest=receipt.get("digest"),
                        delivery=receipt,
                        delivery_error=None,
                        stopped_at=now,
                        stopped_by=locked.withdrawn_by or "system:delivery-withdrawn",
                        stop_reason="撤回交付时 knowmart 已收下，停用这一版",
                    )
            elif found.conversion:
                await self.repo.update_expert(
                    expert.id,
                    review_listing="public",
                    delivery={**(current.delivery or {}), **receipt},
                )
            elif stage_of(current) == "to_deliver":
                await self.repo.update_expert(
                    expert.id,
                    listed_at=now,
                    knowmart_knowbot_id=str(receipt["knowbot_id"]),
                    digest=receipt.get("digest"),
                    delivery=receipt,
                    delivery_error=None,
                )
        if not landed:
            # 撤回、作废与交 knowmart 撞上：
            # - 第一次交付：停 knowmart 那一版；
            # - 自用转公开：只向 knowmart 收回这一版的公开，保留作者的自用上架。
            action = "withdraw_public" if found.conversion else "stop"
            knowbot_id = str(receipt["knowbot_id"])
            try:
                if found.conversion:
                    await self.knowmart.withdraw_public(
                        owner_subject=request.owner_subject,
                        knowbot_id=knowbot_id,
                        version=str(expert.version),
                    )
                else:
                    await self.knowmart.stop(
                        owner_subject=request.owner_subject,
                        knowbot_id=knowbot_id,
                        version=str(expert.version),
                    )
            except AppException as exc:
                log.warning(
                    "knowmart rollback failed delivery=%s action=%s code=%s",
                    found.id,
                    action,
                    exc.code,
                )
                async with self.repo.transaction():
                    latest = await self.repo.get_delivery(found.id, for_update=True)
                    if latest is not None:
                        await self.repo.update_delivery(
                            found.id,
                            consent_ended_at=None,
                            consents={
                                **latest.consents,
                                "pending_knowmart": {
                                    "action": action,
                                    "knowbot_id": knowbot_id,
                                },
                            },
                        )
            raise _conflict("This delivery was withdrawn meanwhile", "delivery_state")
        if not found.conversion:
            await self.experts.tell_unlisted(found.request_id)
        return await self._reload(found.id)

    async def _withdraw(
        self,
        found: DeliveryRecord,
        *,
        by: str,
        reason: str,
        background: bool = False,
    ) -> DeliveryRecord:
        """撤回（作者撤回、平台作废、后台查到作者在 knowbase 撤了审核授权）。

        background：只在锁里看到仍在审、授权没撤时才撤（刚审完的不算作者撤回），
        不是这样就原样回去。"""
        now = self.now()
        async with self.repo.transaction():
            current = await self.repo.get_delivery(found.id, for_update=True)
            assert current is not None
            if background and (
                current.status != "reviewing" or current.consent_ended_at is not None
            ):
                return current
            if not is_open(current):
                raise _conflict("This delivery has ended", "delivery_state")
            await self.repo.update_delivery(
                found.id,
                status="withdrawn",
                withdrawn_by=by,
                withdrawn_at=now,
                withdraw_reason=reason or None,
            )
            if not current.conversion:
                # 第一次交付：回到「等作者提交」，同意门与审核的结论清掉（交付记录留着）
                await self.repo.update_expert(
                    current.expert_id,
                    user_accepted=None,
                    user_decided_by=None,
                    user_decided_at=None,
                    review_approved=None,
                    review_listing=None,
                    review_reason=None,
                    reviewed_by=None,
                    reviewed_at=None,
                    delivery_error=None,
                )
        await self._stop_rerun(current, "delivery withdrawn")
        request = await self.repo.get_request(current.request_id)
        assert request is not None
        await self._close_delivery(
            current,
            request.owner_subject,
            "cancelled",
            void_freeze=not current.conversion,
        )
        return await self._reload(found.id)

    async def _leave_for_close(
        self,
        record: DeliveryRecord,
        owner: str,
        *,
        unended_consents: bool = True,
        pending_void: bool = False,
    ) -> None:
        """提交没成、补撤授权或作废冻结也没成：留一行撤回了的交付，后台补撤扫得到。"""
        consents: dict[str, Any] = {
            **record.consents,
            **(
                {"review": 1, "rerun": 1}
                if unended_consents
                else {"consents_ended": True}
            ),
            **({"pending_void": True} if pending_void else {}),
        }
        try:
            async with self.repo.transaction():
                await self.repo.create_delivery(
                    replace(
                        record,
                        status="withdrawn",
                        withdrawn_by=owner,
                        withdrawn_at=self.now(),
                        withdraw_reason="提交没成，待后台补撤",
                        consents=consents,
                    )
                )
        except AppException as exc:
            log.warning(
                "unended consents not recorded delivery=%s code=%s", record.id, exc.code
            )

    async def _stop_rerun(self, found: DeliveryRecord, reason: str) -> None:
        """在跑的平台重跑停下：叫运行方停，撤这次签的机器授权，测试记平台出错。"""
        if found.rerun_test_id is None:
            return
        await self.creation.stop_rerun(found.rerun_test_id, reason)

    async def _void_frozen_if_unused(
        self,
        expert_id: uuid.UUID,
        *,
        request_id: uuid.UUID,
        owner_subject: str,
    ) -> bool:
        """在锁里确认这一版没有在走的或已上架的交付，再请 knowbase 作废冻结输入。"""
        if self.knowbase is None:
            return True
        async with self.repo.transaction():
            expert = await self.repo.get_expert(expert_id, for_update=True)
            if expert is None or expert.listed_at is not None:
                return True
            deliveries = await self.repo.list_deliveries(expert_id=expert.id)
            if any(
                is_open(d) or (d.status == "approved" and d.delivered_at is not None)
                for d in deliveries
            ):
                return True
            try:
                await self.knowbase.void_frozen(
                    str(request_id), str(expert.version), author=owner_subject
                )
            except AppException as exc:
                log.warning(
                    "frozen binding not voided expert=%s code=%s", expert.id, exc.code
                )
                return False
        return True

    async def _rollback_knowmart(
        self, delivery: DeliveryRecord, owner_subject: str
    ) -> bool:
        pending = delivery.consents.get("pending_knowmart")
        if not isinstance(pending, Mapping):
            return True
        expert = await self.repo.get_expert(delivery.expert_id)
        if expert is None:
            return True
        knowbot_id = str(pending.get("knowbot_id") or expert.knowmart_knowbot_id or "")
        if not knowbot_id:
            return True
        action = str(pending.get("action") or "stop")
        try:
            if action == "withdraw_public":
                await self.knowmart.withdraw_public(
                    owner_subject=owner_subject,
                    knowbot_id=knowbot_id,
                    version=str(expert.version),
                )
            else:
                await self.knowmart.stop(
                    owner_subject=owner_subject,
                    knowbot_id=knowbot_id,
                    version=str(expert.version),
                )
        except AppException as exc:
            log.warning(
                "knowmart rollback not completed delivery=%s action=%s code=%s",
                delivery.id,
                action,
                exc.code,
            )
            return False
        return True

    async def _close_delivery(
        self,
        delivery: DeliveryRecord,
        owner: str,
        reason: str,
        *,
        void_freeze: bool = False,
    ) -> int:
        """审完或撤回后的收尾：撤授权、在锁里确认没别的交付后作废冻结、补做 knowmart
        停用或收回公开。全都成了才标 consent_ended_at；任一项没成留给 close_pending。"""
        ended = bool(delivery.consents.get("consents_ended")) or bool(
            await self._end_consents(delivery, owner, reason, mark_closed=False)
        )
        need_void = void_freeze or bool(delivery.consents.get("pending_void"))
        voided = (
            await self._void_frozen_if_unused(
                delivery.expert_id,
                request_id=delivery.request_id,
                owner_subject=owner,
            )
            if need_void
            else True
        )
        rolled_back = await self._rollback_knowmart(delivery, owner)
        async with self.repo.transaction():
            current = await self.repo.get_delivery(delivery.id, for_update=True)
            if current is None:
                return int(ended and voided and rolled_back)
            consents = dict(current.consents)
            if ended:
                consents["consents_ended"] = True
            if need_void and not voided:
                consents["pending_void"] = True
            else:
                consents.pop("pending_void", None)
            if rolled_back and not isinstance(
                current.consents.get("pending_knowmart"), Mapping
            ):
                pass
            elif rolled_back:
                consents.pop("pending_knowmart", None)
            all_done = (
                ended and voided and rolled_back and "pending_knowmart" not in consents
            )
            await self.repo.update_delivery(
                delivery.id,
                consents=consents,
                consent_ended_at=self.now() if all_done else None,
            )
            return int(all_done)

    async def _end_consents(
        self,
        delivery: DeliveryRecord,
        owner: str,
        reason: str,
        *,
        always: bool = False,
        mark_closed: bool = True,
    ) -> int:
        """审完撤销给审核员（与重跑）的依据；撤成了记下，撤不成由后台补撤。

        always：提交中途失败，不管记没记份数，两种依据都按这次的编号撤一遍。"""
        kinds = [
            k
            for k in ("review", "rerun")
            if always or int(delivery.consents.get(k) or 0) > 0
        ]
        if kinds and self.knowbase is None:
            return 0
        try:
            for kind in kinds:
                assert self.knowbase is not None
                await self.knowbase.end_platform(
                    subject=owner, kind=kind, ref=str(delivery.id), reason=reason
                )
        except AppException as exc:
            log.warning(
                "delivery consents not ended delivery=%s code=%s",
                delivery.id,
                exc.code,
            )
            return 0
        if not mark_closed or await self.repo.get_delivery(delivery.id) is None:
            return 1  # 提交时就没建成：knowbase 那头撤了就行
        async with self.repo.transaction():
            await self.repo.update_delivery(delivery.id, consent_ended_at=self.now())
        return 1

    async def _reload(self, delivery_id: uuid.UUID) -> DeliveryRecord:
        found = await self.repo.get_delivery(delivery_id)
        assert found is not None
        return found
