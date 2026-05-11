"""Pure runtime helpers extracted from build_router.

All functions here take their dependencies (settings/repo/marzban/...) as explicit
keyword arguments. They have no closure state and are safely unit-testable.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from src.vpnbot.device_utils import format_device_limit, next_device_slot
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.services.bot_marzban import MarzbanClient
from utils import build_device_username, build_replacement_username

TrackEventFn = Callable[..., Awaitable[None]]


async def replace_device_slot(
    *,
    telegram_id: int,
    slot: int,
    repo: Repo,
    marzban: MarzbanClient,
) -> tuple[str, str, dict[str, Any]]:
    """Issue a fresh Marzban user for an existing device slot and disable the old one.

    Returns (old_username, new_username, new_user_payload).
    """
    row = await repo.get_device(telegram_id, slot)
    if not row:
        raise RuntimeError("Устройство не найдено в локальной БД.")
    old_username = str(row.get("marzban_username") or "").strip()
    if not old_username:
        raise RuntimeError("Для устройства не найден marzban_username.")
    old_user = await marzban.get_user(old_username)
    if not old_user:
        raise RuntimeError(f"Старый профиль {old_username} не найден в Marzban.")

    new_username = build_replacement_username(telegram_id, slot)
    new_user = await marzban.create_user(
        username=new_username,
        expire=int(old_user.get("expire", 0) or 0),
        data_limit=int(old_user.get("data_limit", 0) or 0),
    )

    await repo.upsert_device(
        telegram_id,
        slot,
        new_username,
        row.get("device_name"),
    )
    if slot == 1:
        await repo.upsert_user(telegram_id, new_username)

    try:
        await marzban.modify_user(old_username, {"status": "disabled"})
    except Exception:
        logging.exception("device_replace: failed to disable old username %s", old_username)

    return old_username, new_username, new_user


async def list_replaceable_devices(
    telegram_id: int,
    *,
    repo: Repo,
    marzban: MarzbanClient,
) -> list[dict[str, Any]]:
    """Return only those device rows whose Marzban user exists and is active."""
    devices = await repo.list_devices(telegram_id)
    result: list[dict[str, Any]] = []
    for row in devices:
        username = str(row.get("marzban_username") or "").strip()
        if not username:
            continue
        user = await marzban.get_user(username)
        if not user:
            continue
        status = str(user.get("status", "unknown"))
        if status != "active":
            continue
        result.append(row)
    return result


async def bind_web_order_to_user(
    *,
    telegram_id: int,
    order_id: str,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Any,
    track_event: TrackEventFn,
) -> tuple[bool, str]:
    """Bind a paid web order to a Telegram account, claiming or syncing the slot.

    Returns (ok, user_facing_message).
    """
    order = await repo.get_web_order(order_id)
    if not order:
        return False, "Заказ не найден. Проверьте ссылку привязки."

    status = str(order.get("status") or "").strip().lower()
    if status != "paid_applied":
        return False, (
            "Оплата еще не подтверждена на сайте.\n"
            "Вернитесь на сайт, нажмите «Проверить оплату», затем повторите привязку."
        )

    username = str(order.get("marzban_username") or "").strip()
    if not username:
        return False, "Доступ еще не подготовлен. Попробуйте повторить через 10-20 секунд."

    user_in_mz = await marzban.get_user(username)
    if not user_in_mz:
        return False, "Профиль в VPN-панели не найден. Напишите в поддержку."

    async def ensure_slot_username(
        *,
        slot: int,
        source_username: str,
        source_user: dict[str, Any],
    ) -> tuple[str, bool]:
        target_username = build_device_username(telegram_id, slot)
        if source_username == target_username:
            if slot <= 1:
                await repo.upsert_user(telegram_id, target_username)
            else:
                await repo.upsert_device(telegram_id, slot, target_username, "Сайт")
            await repo.attach_web_order_access(
                order_id=order_id,
                marzban_username=target_username,
            )
            return target_username, False

        source_expire = int(source_user.get("expire") or 0)
        source_limit = int(source_user.get("data_limit") or 0)
        target_user = await marzban.get_user(target_username)
        if target_user:
            target_owner_dev = await repo.get_device_by_username(target_username)
            target_owner_usr = await repo.get_user_by_username(target_username)
            target_owner_tg = None
            if target_owner_dev:
                target_owner_tg = int(target_owner_dev["telegram_id"])
            elif target_owner_usr:
                target_owner_tg = int(target_owner_usr["telegram_id"])
            if target_owner_tg is not None and target_owner_tg != telegram_id:
                raise RuntimeError("Целевой слот уже занят другим Telegram-аккаунтом.")

            target_expire = int(target_user.get("expire") or 0)
            target_limit = int(target_user.get("data_limit") or 0)
            patch: dict[str, Any] = {"status": "active"}
            if source_expire > target_expire:
                patch["expire"] = source_expire
            if source_limit > target_limit:
                patch["data_limit"] = source_limit
            if patch:
                await marzban.modify_user(target_username, patch)
        else:
            await marzban.create_user(
                username=target_username,
                expire=source_expire,
                data_limit=source_limit,
            )

        if slot <= 1:
            await repo.upsert_user(telegram_id, target_username)
        else:
            await repo.upsert_device(telegram_id, slot, target_username, "Сайт")
        await repo.attach_web_order_access(
            order_id=order_id,
            marzban_username=target_username,
        )
        try:
            await marzban.modify_user(source_username, {"status": "disabled"})
        except Exception:
            logging.exception(
                "webbind: failed to disable source username %s -> %s",
                source_username,
                target_username,
            )
        return target_username, True

    owner_dev = await repo.get_device_by_username(username)
    owner_usr = await repo.get_user_by_username(username)
    owner_tg = None
    if owner_dev:
        owner_tg = int(owner_dev["telegram_id"])
    elif owner_usr:
        owner_tg = int(owner_usr["telegram_id"])

    if owner_tg is not None and owner_tg != telegram_id:
        return False, "Этот доступ уже привязан к другому Telegram-аккаунту."

    if owner_tg == telegram_id:
        slot = int(owner_dev["device_id"]) if owner_dev else 1
        try:
            _, migrated = await ensure_slot_username(
                slot=slot,
                source_username=username,
                source_user=user_in_mz,
            )
        except Exception:
            logging.exception(
                "webbind: failed to sync already-bound order=%s tg=%s",
                order_id,
                telegram_id,
            )
            return False, "Привязка уже есть, но не удалось синхронизировать доступ. Напишите в поддержку."
        if migrated:
            return True, (
                f"Готово ✅ Доступ с сайта синхронизирован как устройство #{slot}. "
                "Нажмите «🔑 Получить конфиг»."
            )
        return True, "Этот доступ уже привязан к вашему Telegram. Нажмите «🔑 Получить конфиг»."

    current_user = await repo.get_user(telegram_id)
    if current_user is None:
        try:
            target_username, _ = await ensure_slot_username(
                slot=1,
                source_username=username,
                source_user=user_in_mz,
            )
        except Exception:
            logging.exception(
                "webbind: failed for new tg=%s order=%s username=%s",
                telegram_id,
                order_id,
                username,
            )
            return False, "Не удалось привязать доступ к Telegram. Напишите в поддержку."
        await track_event(
            "web_order_bound",
            telegram_id=telegram_id,
            event_value="slot_1",
            event_meta={
                "order_id": order_id,
                "from_marzban_username": username,
                "marzban_username": target_username,
            },
        )
        return True, "Готово ✅ Доступ с сайта привязан к Telegram. Нажмите «🔑 Получить конфиг»."

    devices = await repo.list_devices(telegram_id)
    for row in devices:
        if str(row.get("marzban_username") or "").strip() == username:
            return True, "Этот доступ уже привязан к вашему Telegram. Нажмите «🔑 Получить конфиг»."

    used_slots = {int(row.get("device_id") or 0) for row in devices}
    slot = next_device_slot(used_slots, settings.device_limit)
    if slot is None:
        return False, (
            f"Достигнут лимит устройств ({format_device_limit(settings.device_limit)}).\n"
            "Освободите слот через «🔁 Заменить устройство» или напишите в поддержку."
        )

    try:
        target_username, _ = await ensure_slot_username(
            slot=slot,
            source_username=username,
            source_user=user_in_mz,
        )
    except Exception:
        logging.exception(
            "webbind: failed for tg=%s order=%s slot=%s username=%s",
            telegram_id,
            order_id,
            slot,
            username,
        )
        return False, "Не удалось привязать доступ как устройство. Напишите в поддержку."
    await track_event(
        "web_order_bound",
        telegram_id=telegram_id,
        event_value=f"slot_{slot}",
        event_meta={
            "order_id": order_id,
            "from_marzban_username": username,
            "marzban_username": target_username,
        },
    )
    return True, f"Готово ✅ Доступ с сайта привязан как устройство #{slot}. Нажмите «🔑 Получить конфиг»."
