"""Safe admin-driven resolutions for Marzban/DB drift findings.

Every resolver is a pure async function that takes a `DriftFinding` plus the
required infra (repo/marzban/settings), performs the smallest possible change,
and returns a structured `ResolutionResult`. Every successful resolution is
also persisted via `repo.log_event(event_type="drift_resolved", ...)` so the
admin trail is reconstructible from the events table.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.marzban_sync import (
    DRIFT_IGNORED_EVENT_TYPE,
    KIND_MISSING_IN_MARZBAN,
    KIND_WEB_ORDER_NO_ACCESS,
    DriftFinding,
)
from src.vpnbot.services.bot_marzban import MarzbanClient

EVENT_DRIFT_RESOLVED = "drift_resolved"
EVENT_DRIFT_IGNORED = DRIFT_IGNORED_EVENT_TYPE


@dataclass(frozen=True)
class ResolutionResult:
    """Outcome of one drift-resolution attempt."""

    ok: bool
    message: str
    action: str = ""
    finding_id: str = ""


def _trial_recovery_params(settings: Any) -> tuple[int, int]:
    """Conservative default expire/data_limit when no plan context is available.

    Returns (days, gb). We use trial values so a recreate cannot accidentally
    grant a long paid term: admin can extend later via existing tools.
    """
    days = max(1, int(getattr(settings, "trial_days", 1) or 1))
    gb = max(0, int(getattr(settings, "trial_gb", 0) or 0))
    return days, gb


def _make_web_username(order_id: str, suffix: int = 0) -> str:
    base = f"web_{order_id[:10]}"
    if suffix <= 0:
        return base
    return f"{base}_{suffix}"


async def recreate_missing_marzban_user(
    finding: DriftFinding,
    *,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Any,
    actor_tg: int | None = None,
) -> ResolutionResult:
    """Recreate the missing Marzban user with conservative recovery params.

    Recreate is offered only for `tg_*` usernames. Web-order usernames
    (`web_*`) should not be re-created blindly because the payment context is
    typically gone; admin should use `drop_db_ref` or `ignore` instead.
    """
    if finding.kind != KIND_MISSING_IN_MARZBAN:
        return ResolutionResult(False, f"Не поддерживается для kind={finding.kind}.")
    username = str(finding.payload.get("username") or "").strip()
    if not username:
        return ResolutionResult(False, "В finding отсутствует username.")
    if not username.startswith("tg_"):
        return ResolutionResult(
            False,
            f"Recreate доступен только для tg_* пользователей (получено: {username}).",
        )

    existing = await marzban.get_user(username)
    if existing:
        await repo.log_event(
            event_type=EVENT_DRIFT_RESOLVED,
            telegram_id=actor_tg,
            event_value=finding.finding_id,
            event_meta={"action": "recreate", "result": "already_exists", "username": username},
        )
        return ResolutionResult(
            True,
            f"В Marzban уже существует: {username} (повторное создание не требуется).",
            action="recreate",
            finding_id=finding.finding_id,
        )

    days, gb = _trial_recovery_params(settings)
    expire = int(time.time()) + days * 86400
    data_limit = int(gb) * 1_000_000_000

    try:
        await marzban.create_user(username=username, expire=expire, data_limit=data_limit)
    except Exception as exc:
        logging.exception("drift_resolver.recreate: Marzban create failed for %s", username)
        return ResolutionResult(False, f"Marzban create_user провалился: {exc}")

    await repo.log_event(
        event_type=EVENT_DRIFT_RESOLVED,
        telegram_id=actor_tg,
        event_value=finding.finding_id,
        event_meta={
            "action": "recreate",
            "username": username,
            "days": days,
            "gb": gb,
        },
    )
    return ResolutionResult(
        True,
        f"Создан в Marzban: {username} (восстановительный период {days} дн, {gb} GB).",
        action="recreate",
        finding_id=finding.finding_id,
    )


async def drop_missing_marzban_db_ref(
    finding: DriftFinding,
    *,
    repo: Repo,
    actor_tg: int | None = None,
) -> ResolutionResult:
    """Remove DB references pointing at a Marzban user that no longer exists.

    Touches only `users` and `devices`. `web_orders` rows are left alone:
    they are an immutable financial audit trail and should be retired via
    a separate `set_web_order_status('manual_removed', ...)` flow if needed.
    """
    if finding.kind != KIND_MISSING_IN_MARZBAN:
        return ResolutionResult(False, f"Не поддерживается для kind={finding.kind}.")
    if repo.conn is None:
        return ResolutionResult(False, "Repo не открыт.")
    username = str(finding.payload.get("username") or "").strip()
    refs = list(finding.payload.get("refs") or [])
    if not username:
        return ResolutionResult(False, "В finding отсутствует username.")

    removed: list[str] = []
    skipped: list[str] = []

    for ref in refs:
        source = str(ref.get("source") or "")
        if source == "users":
            # `users.marzban_username` is NOT NULL in the current schema, so we
            # soft-clear with an empty string. Downstream code treats blank as
            # "no marzban access" (same as a fresh signup before slot 1 issue).
            cur = await repo.conn.execute(
                "UPDATE users SET marzban_username = '' WHERE marzban_username = ?",
                (username,),
            )
            await repo.conn.commit()
            removed.append(f"users.marzban_username<-'' (rows={cur.rowcount})")
        elif source == "devices":
            tg_id = int(ref.get("telegram_id") or 0)
            device_id = int(ref.get("device_id") or 0)
            if tg_id <= 0 or device_id <= 0:
                skipped.append(f"devices(missing_keys:tg={tg_id},slot={device_id})")
                continue
            cur = await repo.conn.execute(
                "DELETE FROM devices WHERE telegram_id = ? AND device_id = ? AND marzban_username = ?",
                (tg_id, device_id, username),
            )
            await repo.conn.commit()
            removed.append(f"devices.row(tg={tg_id},slot={device_id},rows={cur.rowcount})")
        elif source == "web_orders":
            skipped.append("web_orders(audit_trail_preserved)")
        else:
            skipped.append(f"{source}(unknown_source)")

    if not removed:
        return ResolutionResult(
            False,
            "Нет безопасных DB-ссылок для удаления. "
            + (f"Пропущено: {', '.join(skipped)}" if skipped else ""),
        )

    await repo.log_event(
        event_type=EVENT_DRIFT_RESOLVED,
        telegram_id=actor_tg,
        event_value=finding.finding_id,
        event_meta={
            "action": "drop_db_ref",
            "username": username,
            "removed": removed,
            "skipped": skipped,
        },
    )
    return ResolutionResult(
        True,
        f"Удалено DB-ссылок: {len(removed)} ({', '.join(removed)})."
        + (f" Пропущено: {', '.join(skipped)}." if skipped else ""),
        action="drop_db_ref",
        finding_id=finding.finding_id,
    )


async def retry_web_order_access(
    finding: DriftFinding,
    *,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Any,
    actor_tg: int | None = None,
) -> ResolutionResult:
    """For a paid_applied web order missing access, create Marzban user and attach it.

    The plan term/quota is taken from the order row itself (days, gb), not
    from the current settings — that's the value the customer already paid for.
    """
    if finding.kind != KIND_WEB_ORDER_NO_ACCESS:
        return ResolutionResult(False, f"Не поддерживается для kind={finding.kind}.")
    order_id = str(finding.payload.get("order_id") or "").strip()
    if not order_id:
        return ResolutionResult(False, "В finding отсутствует order_id.")

    order = await repo.get_web_order(order_id)
    if not order:
        return ResolutionResult(False, f"Заказ {order_id} не найден в БД.")
    if str(order.get("status") or "") != "paid_applied":
        return ResolutionResult(
            False,
            f"Статус заказа изменился ({order.get('status')}). Повторите аудит.",
        )
    existing_username = str(order.get("marzban_username") or "").strip()
    if existing_username:
        return ResolutionResult(
            True,
            f"К заказу уже привязан username: {existing_username}. Действие не требуется.",
            action="retry_web_order",
            finding_id=finding.finding_id,
        )

    days = max(1, int(order.get("days") or 1))
    gb = max(0, int(order.get("gb") or 0))
    expire = int(time.time()) + days * 86400
    data_limit = int(gb) * 1_000_000_000
    new_username = _make_web_username(order_id)
    for i in range(0, 30):
        candidate = _make_web_username(order_id, suffix=i)
        if not await marzban.get_user(candidate):
            new_username = candidate
            break
    else:
        new_username = f"web_{uuid.uuid4().hex[:10]}"

    try:
        await marzban.create_user(username=new_username, expire=expire, data_limit=data_limit)
    except Exception as exc:
        logging.exception("drift_resolver.retry_web_order: Marzban create failed for %s", new_username)
        return ResolutionResult(False, f"Marzban create_user провалился: {exc}")

    try:
        await repo.attach_web_order_access(order_id=order_id, marzban_username=new_username)
    except Exception as exc:
        logging.exception("drift_resolver.retry_web_order: attach failed for order=%s", order_id)
        return ResolutionResult(
            False,
            f"Marzban-пользователь создан ({new_username}), но не удалось привязать к заказу: {exc}",
        )

    await repo.log_event(
        event_type=EVENT_DRIFT_RESOLVED,
        telegram_id=actor_tg,
        event_value=finding.finding_id,
        event_meta={
            "action": "retry_web_order",
            "order_id": order_id,
            "marzban_username": new_username,
            "days": days,
            "gb": gb,
        },
    )
    return ResolutionResult(
        True,
        f"Создан Marzban-пользователь {new_username} и привязан к заказу {order_id} ({days}д, {gb}GB).",
        action="retry_web_order",
        finding_id=finding.finding_id,
    )


async def ignore_drift(
    finding: DriftFinding,
    *,
    repo: Repo,
    actor_tg: int | None = None,
    note: str = "",
) -> ResolutionResult:
    """Mark a finding as known/ignored. Persisted via events table for audit trail."""
    await repo.log_event(
        event_type=EVENT_DRIFT_IGNORED,
        telegram_id=actor_tg,
        event_value=finding.finding_id,
        event_meta={
            "kind": finding.kind,
            "summary": finding.summary,
            "note": note,
        },
    )
    return ResolutionResult(
        True,
        f"Отмечено как известное/игнорируемое: {finding.finding_id}.",
        action="ignore",
        finding_id=finding.finding_id,
    )
