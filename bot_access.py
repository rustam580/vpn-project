from __future__ import annotations

import logging
import time
from typing import Any

BYTES_IN_GB = 1024**3


def build_username(telegram_id: int) -> str:
    return f"tg_{telegram_id}"


def build_device_username(telegram_id: int, device_id: int) -> str:
    if device_id <= 1:
        return build_username(telegram_id)
    return f"tg_{telegram_id}_d{device_id}"


async def ensure_user(
    *,
    telegram_id: int,
    repo: Any,
    marzban: Any,
    settings: Any,
    create_if_missing: bool,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    created = False
    row = await repo.get_user(telegram_id)
    if row:
        username = row["marzban_username"]
        user = await marzban.get_user(username)
        if user:
            await repo.upsert_user(telegram_id, username)
            return username, user, created

    username = build_username(telegram_id)
    user = await marzban.get_user(username)
    if user:
        await repo.upsert_user(telegram_id, username)
        return username, user, created

    if not create_if_missing:
        return None, None, created

    user = await marzban.create_user(
        username=username,
        expire=int(time.time()) + settings.trial_days * 24 * 3600,
        data_limit=0 if settings.trial_gb <= 0 else settings.trial_gb * BYTES_IN_GB,
    )
    await repo.upsert_user(telegram_id, username)
    created = True
    return username, user, created


async def ensure_device(
    *,
    telegram_id: int,
    device_id: int,
    repo: Any,
    marzban: Any,
    settings: Any,
    create_if_missing: bool,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    if device_id < 1:
        return None, None, False
    if settings.device_limit > 0 and device_id > settings.device_limit:
        return None, None, False

    if device_id == 1:
        return await ensure_user(
            telegram_id=telegram_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=create_if_missing,
        )

    created = False
    row = await repo.get_device(telegram_id, device_id)
    if row:
        username = row["marzban_username"]
        user = await marzban.get_user(username)
        if user:
            return username, user, created

    username = build_device_username(telegram_id, device_id)
    user = await marzban.get_user(username)
    if user:
        await repo.upsert_device(telegram_id, device_id, username)
        return username, user, created

    if not create_if_missing:
        return None, None, created

    primary_user = None
    primary_row = await repo.get_user(telegram_id)
    if primary_row:
        primary_user = await marzban.get_user(primary_row["marzban_username"])

    if primary_user:
        expire = int(primary_user.get("expire", 0) or 0)
        data_limit = int(primary_user.get("data_limit", 0) or 0)
        status = str(primary_user.get("status", "active"))
        user = await marzban.create_user(
            username=username,
            expire=expire,
            data_limit=data_limit,
        )
        if status and status != "active":
            await marzban.modify_user(username, {"status": status})
    else:
        user = await marzban.create_user(
            username=username,
            expire=int(time.time()) + settings.trial_days * 24 * 3600,
            data_limit=0 if settings.trial_gb <= 0 else settings.trial_gb * BYTES_IN_GB,
        )
    await repo.upsert_device(telegram_id, device_id, username)
    created = True
    return username, user, created


async def extend_access(
    *,
    telegram_id: int,
    days: int,
    gb: int,
    repo: Any,
    marzban: Any,
    settings: Any,
) -> dict[str, Any]:
    username, user, _ = await ensure_user(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=False,
    )
    if username is None:
        username = build_username(telegram_id)
        user = await marzban.get_user(username)

    if user:
        old_exp = int(user.get("expire", 0) or 0)
        if days <= 0:
            new_exp = 0
        else:
            new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
        if gb <= 0:
            new_limit = 0
        else:
            old_limit = int(user.get("data_limit", 0) or 0)
            new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB
        updated = await marzban.modify_user(
            username,
            {"expire": new_exp, "data_limit": new_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=0 if days <= 0 else int(time.time()) + days * 24 * 3600,
            data_limit=0 if gb <= 0 else gb * BYTES_IN_GB,
        )
    await repo.upsert_user(telegram_id, username)
    return updated


async def extend_access_all_devices(
    *,
    telegram_id: int,
    days: int,
    gb: int,
    repo: Any,
    marzban: Any,
    settings: Any,
) -> dict[str, Any]:
    usernames = await repo.list_device_usernames(telegram_id)
    if not usernames:
        username, _, _ = await ensure_device(
            telegram_id=telegram_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=True,
        )
        if not username:
            return {}
        usernames = [username]

    updated_primary: dict[str, Any] | None = None
    for username in usernames:
        user = await marzban.get_user(username)
        if user:
            old_exp = int(user.get("expire", 0) or 0)
            if days <= 0:
                new_exp = 0
            else:
                new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
            if gb <= 0:
                new_limit = 0
            else:
                old_limit = int(user.get("data_limit", 0) or 0)
                new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB
            updated = await marzban.modify_user(
                username,
                {"expire": new_exp, "data_limit": new_limit, "status": "active"},
            )
        else:
            updated = await marzban.create_user(
                username=username,
                expire=0 if days <= 0 else int(time.time()) + days * 24 * 3600,
                data_limit=0 if gb <= 0 else gb * BYTES_IN_GB,
            )
        if updated_primary is None:
            updated_primary = updated
    return updated_primary or {}


async def extend_access_device(
    *,
    telegram_id: int,
    device_id: int,
    days: int,
    gb: int,
    repo: Any,
    marzban: Any,
    settings: Any,
) -> dict[str, Any]:
    username, user, _ = await ensure_device(
        telegram_id=telegram_id,
        device_id=device_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=True,
    )
    if not username:
        raise RuntimeError("Не удалось получить слот устройства.")

    if user:
        old_exp = int(user.get("expire", 0) or 0)
        if days <= 0:
            new_exp = 0
        else:
            new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
        if gb <= 0:
            new_limit = 0
        else:
            old_limit = int(user.get("data_limit", 0) or 0)
            new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB
        updated = await marzban.modify_user(
            username,
            {"expire": new_exp, "data_limit": new_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=0 if days <= 0 else int(time.time()) + days * 24 * 3600,
            data_limit=0 if gb <= 0 else gb * BYTES_IN_GB,
        )
    await repo.upsert_device(telegram_id, device_id, username)
    return updated


async def sync_expire_across_devices(
    *,
    telegram_id: int,
    repo: Any,
    marzban: Any,
    mode: str = "max",
    source_slot: int | None = None,
) -> tuple[int, int, int, int]:
    rows = await repo.list_devices(telegram_id)
    if not rows:
        primary = await repo.get_user(telegram_id)
        if primary:
            rows = [
                {
                    "device_id": 1,
                    "marzban_username": str(primary.get("marzban_username") or "").strip(),
                    "device_name": None,
                }
            ]
        else:
            return 0, 0, 0, 0

    found: list[tuple[int, str, int]] = []
    missing_count = 0
    for row in rows:
        device_id = int(row.get("device_id") or 0)
        username = str(row.get("marzban_username") or "").strip()
        if not username:
            missing_count += 1
            continue
        user = await marzban.get_user(username)
        if not user:
            missing_count += 1
            continue
        expire = int(user.get("expire", 0) or 0)
        found.append((device_id, username, expire))

    if not found:
        return 0, 0, 0, missing_count

    if mode == "min":
        target_expire = min(expire for _, _, expire in found)
    elif mode == "slot":
        if source_slot is None or source_slot < 1:
            raise ValueError("Укажите корректный слот для синхронизации.")
        slot_expire = next((expire for d_id, _, expire in found if d_id == source_slot), None)
        if slot_expire is None:
            raise ValueError(f"Слот {source_slot} не найден в Marzban.")
        target_expire = slot_expire
    else:
        target_expire = max(expire for _, _, expire in found)

    changed = 0
    for _, username, expire in found:
        if expire == target_expire:
            continue
        await marzban.modify_user(username, {"expire": target_expire})
        changed += 1

    return target_expire, changed, len(found), missing_count


async def set_permanent_access(
    *,
    telegram_id: int,
    gb: int,
    repo: Any,
    marzban: Any,
    settings: Any,
) -> dict[str, Any]:
    username, user, _ = await ensure_user(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=False,
    )
    if username is None:
        username = build_username(telegram_id)
        user = await marzban.get_user(username)

    if gb <= 0:
        new_limit = 0
    else:
        old_limit = int(user.get("data_limit", 0) or 0) if user else 0
        new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB

    if user:
        updated = await marzban.modify_user(
            username,
            {"expire": 0, "data_limit": new_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=0,
            data_limit=new_limit,
        )
    await repo.upsert_user(telegram_id, username)
    return updated


async def extend_access_days_only(
    *,
    telegram_id: int,
    days: int,
    repo: Any,
    marzban: Any,
    settings: Any,
) -> dict[str, Any]:
    username, user, _ = await ensure_user(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=False,
    )
    if username is None:
        username = build_username(telegram_id)
        user = await marzban.get_user(username)

    if user:
        old_exp = int(user.get("expire", 0) or 0)
        old_limit = int(user.get("data_limit", 0) or 0)
        new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
        updated = await marzban.modify_user(
            username,
            {"expire": new_exp, "data_limit": old_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=int(time.time()) + days * 24 * 3600,
            data_limit=0 if settings.trial_gb <= 0 else settings.trial_gb * BYTES_IN_GB,
        )
    await repo.upsert_user(telegram_id, username)
    return updated


async def apply_referral_bonus_if_needed(
    *,
    paid_telegram_id: int,
    repo: Any,
    marzban: Any,
    settings: Any,
    bot: Any | None = None,
    notify_access_updated_fn: Any | None = None,
) -> None:
    bonus_days = max(0, settings.referral_bonus_days)
    if bonus_days <= 0:
        return

    referrer_id = await repo.claim_referral_bonus(paid_telegram_id)
    if referrer_id is None:
        return

    try:
        updated = await extend_access_days_only(
            telegram_id=referrer_id,
            days=bonus_days,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
    except Exception:
        await repo.rollback_referral_bonus_claim(paid_telegram_id, referrer_id)
        raise

    if bot is None or notify_access_updated_fn is None:
        return
    try:
        await notify_access_updated_fn(
            bot,
            referrer_id,
            updated,
            f"🎁 Реферальный бонус: +{bonus_days} дн. за приглашенного пользователя.",
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
    except Exception:
        logging.exception("Referral bonus: failed to notify referrer %s", referrer_id)
