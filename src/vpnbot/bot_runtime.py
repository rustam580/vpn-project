
import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from app_texts import (
    build_start_text,
    build_support_templates_text,
    build_user_faq_text,
)
from src.vpnbot.payment_helpers import (
    apply_paid_payment,
    apply_referral_bonus_if_needed,
    cryptobot_check_invoice,
    cryptobot_create_invoice,
    yookassa_check_payment,
    yookassa_create_payment,
)
from config import (
    _absolutize_subscription_link,
    _parse_plans_json,
    _plans_to_json,
    _preset_plans,
    env_bool,
    normalize_config_delivery_mode,
    normalize_public_base_url,
    parse_admin_ids,
    parse_int_csv,
    Settings,
)
from models import MarzbanUser
from src.vpnbot.services.payment_flow import (
    check_and_apply_payment as pf_check_and_apply_payment,
)
from src.vpnbot.bot_formatters import (
    admin_plans_text,
    format_expire,
    format_last_online,
    format_limit,
    format_time_left,
    format_used,
    plan_gb_text,
    plan_title,
    plans_list_text,
)
from src.vpnbot.device_utils import (
    _device_label,
    _short_label,
    format_device_limit,
    next_device_slot,
    normalize_device_name,
)
from src.vpnbot.env_utils import (
    coerce_env_value,
    normalize_channel_url,
    update_env_file,
)
from src.vpnbot.message_utils import (
    config_import_hint_text,
    quick_connect_guide_text,
    split_message,
)
from src.vpnbot.messaging import (
    _render_config_block,
    collect_device_links,
    notify_access_updated,
    send_configs_in_chat,
    send_configs_in_chat_to_bot,
    send_device_links,
    send_device_links_to_bot,
    send_links,
    send_status,
    send_status_to_bot,
)
from src.vpnbot.notifications import (
    notify_admin_payment,
    notify_admin_requeued_processing,
    notify_admin_worker_alert,
)
from src.vpnbot.permissions import is_admin
from src.vpnbot.deploy_reports import (
    broadcast_menu_update,
    deploy_report_worker,
    send_deploy_report_if_any,
)
from src.vpnbot.worker_runtime import (
    auto_renew_plan,
    auto_renew_provider,
    cryptobot_auto_worker,
    daily_ops_report_worker,
    find_plan,
    send_daily_report,
    subscription_migration_worker,
    subscription_renewal_worker,
    yookassa_auto_worker,
)
from src.vpnbot.handlers.bot_handlers_admin import (
    AdminMessageDeps,
    register_admin_message_handlers,
)
from src.vpnbot.handlers.bot_handlers_callbacks_user import (
    UserCallbackDeps,
    register_user_callback_handlers,
)
from src.vpnbot.handlers.bot_handlers_callbacks_admin import (
    AdminCallbackDeps,
    register_admin_callback_handlers,
)
from src.vpnbot.handlers.bot_handlers_fallback import (
    FallbackDeps,
    register_fallback_handler,
)
from src.vpnbot.handlers.bot_handlers_user import (
    UserMessageDeps,
    register_user_message_handlers,
)
from src.vpnbot.keyboards.bot_keyboards import (
    admin_panel_keyboard,
    admin_plans_keyboard,
    broadcast_confirm_keyboard,
    broadcast_format_label,
    broadcast_next_format,
    broadcast_parse_mode,
    buy_plan_keyboard,
    buy_target_keyboard,
    device_methods_keyboard,
    keyboard_for_user,
    more_actions_keyboard,
    pay_action_keyboard,
    payment_methods_keyboard,
    renewal_actions_keyboard,
)
from src.vpnbot.bot_access import (
    ensure_device,
    ensure_user,
    extend_access,
    extend_access_all_devices,
    extend_access_days_only,
    extend_access_device,
    set_permanent_access,
    sync_expire_across_devices,
)
from src.vpnbot.services.bot_marzban import MarzbanClient
from src.vpnbot.bot_network import _parse_sar_dev_output
from src.vpnbot.bot_ops import (
    build_admin_stats_text,
    build_ops_report_text,
    build_ref_top_text,
)
from src.vpnbot.bot_rate_limit import InMemoryRateLimiter
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.bot_router_helpers import (
    BroadcastPreviewContext,
    UserLookupContext,
    send_broadcast_preview as send_broadcast_preview_impl,
    send_user_lookup as send_user_lookup_impl,
)
from utils import (
    build_device_username,
    build_replacement_username,
    build_username,
    build_web_bind_payload,
    extract_links,
    extract_start_payload,
    extract_subscription_links,
    parse_referrer_from_payload,
    parse_web_order_from_payload,
    select_delivery_links,
    status_text,
)

BYTES_IN_GB = 1024**3


def enabled_payment_providers(settings: Settings) -> list[str]:
    providers: list[str] = []
    if settings.cryptobot_enabled():
        providers.append("crypto")
    if settings.yookassa_enabled():
        providers.append("card")
    return providers


def _link_copy_keyboard(link: str) -> InlineKeyboardMarkup | None:
    return None


def _link_preview(link: str) -> str:
    link = link.strip()
    if len(link) <= 28:
        return link
    prefix = ""
    core = link
    if "://" in link:
        proto, rest = link.split("://", 1)
        prefix = f"{proto}://"
        core = rest
    if len(core) <= 20:
        return link
    return f"{prefix}{core[:10]}...{core[-8:]}"


ENV_EDITABLE_KEYS: dict[str, str] = {
    "TRIAL_DAYS": "int",
    "TRIAL_GB": "int",
    "PAY_DAYS": "int",
    "PAY_GB": "int",
    "PAY_RUB": "float",
    "PLANS_JSON": "str",
    "DEVICE_LIMIT": "int",
    "DEVICE_ADD_RUB": "float",
    "REFERRAL_BONUS_DAYS": "int",
    "SUPPORT_USERNAME": "str",
    "SUPPORT_TEXT": "str",
    "CHANNEL_URL": "str",
    "CONFIG_DELIVERY_MODE": "str",
    "SUBSCRIPTION_PUBLIC_BASE_URL": "str",
    "DEPLOY_BROADCAST_USERS": "bool",
    "OPS_REPORT_ENABLED": "bool",
    "OPS_REPORT_HOUR": "int",
    "OPS_REPORT_MINUTE": "int",
    "YOOKASSA_POLL_SECONDS": "int",
    "YOOKASSA_SHOP_ID": "str",
    "YOOKASSA_SECRET_KEY": "str",
    "YOOKASSA_RETURN_URL": "str",
    "PAYMENT_PROCESSING_REQUEUE_SECONDS": "int",
    "RENEWAL_ALERTS_ENABLED": "bool",
    "RENEWAL_ALERT_INTERVAL_SEC": "int",
    "RENEWAL_REMINDER_HOURS": "str",
    "RENEWAL_EXPIRED_ALERT_ENABLED": "bool",
    "ADMIN_ALERTS_ENABLED": "bool",
    "ADMIN_ALERT_COOLDOWN_SEC": "int",
    "AUTO_RENEW_INVOICE_ENABLED": "bool",
    "AUTO_RENEW_INVOICE_HOURS_BEFORE": "int",
    "AUTO_RENEW_INVOICE_PROVIDER": "str",
    "AUTO_RENEW_INVOICE_PLAN_KEY": "str",
    "AUTO_RENEW_INVOICE_TARGET": "str",
    "SUB_MIGRATION_REMINDER_ENABLED": "bool",
    "SUB_MIGRATION_REMINDER_INTERVAL_SEC": "int",
    "SUB_MIGRATION_REMINDER_LOOKBACK_DAYS": "int",
    "SUB_MIGRATION_REMINDER_COOLDOWN_HOURS": "int",
    "SUB_MIGRATION_REMINDER_BATCH": "int",
    "SUBSCRIPTION_HITS_RETENTION_DAYS": "int",
}


def _configs_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup | None:
    if not items:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    if len(items) <= 2:
        for index, label in items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Показать #{index} ({_short_label(label)})",
                        callback_data=f"cfg:show:{index}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="Показать все в чате", callback_data="cfg:showall")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _devices_rename_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    if not devices:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in devices:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{device_id}. {_short_label(label, limit=22)}",
                    callback_data=f"devrename:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="devrename:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _devices_replace_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    if not devices:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in devices:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{device_id}. {_short_label(label, limit=22)}",
                    callback_data=f"devreplace:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="devreplace:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _device_replace_confirm_keyboard(device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"devreplace_confirm:{device_id}:yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"devreplace_confirm:{device_id}:no",
                ),
            ]
        ]
    )


async def check_and_apply_payment(
    *,
    provider: str,
    external_id: str,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    bot: Bot | None = None,
) -> tuple[str, dict[str, Any] | None]:
    return await pf_check_and_apply_payment(
        provider=provider,
        external_id=external_id,
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        bot=bot,
        cryptobot_check_invoice_fn=cryptobot_check_invoice,
        yookassa_check_payment_fn=yookassa_check_payment,
        apply_paid_payment_fn=apply_paid_payment,
    )


def build_router(settings: Settings, repo: Repo, marzban: MarzbanClient) -> Router:
    router = Router()
    message_limiter = InMemoryRateLimiter(
        limit=settings.user_rate_limit_count,
        window_sec=settings.user_rate_limit_window_sec,
    )
    callback_limiter = InMemoryRateLimiter(
        limit=settings.callback_rate_limit_count,
        window_sec=settings.callback_rate_limit_window_sec,
    )
    bot_username_cache: str | None = None
    pending_device_rename: dict[int, int] = {}
    pending_device_add_prompt: set[int] = set()
    pending_issue: set[int] = set()
    pending_user_lookup: set[int] = set()
    pending_broadcast_prompt: set[int] = set()
    pending_broadcast_text: dict[int, str] = {}
    pending_broadcast_format: dict[int, str] = {}
    pending_broadcast_buttons: dict[int, bool] = {}

    async def track_event(
        event_type: str,
        *,
        telegram_id: int | None = None,
        event_value: str = "",
        event_meta: dict[str, Any] | None = None,
    ) -> None:
        try:
            await repo.log_event(
                event_type=event_type,
                telegram_id=telegram_id,
                event_value=event_value,
                event_meta=event_meta,
            )
        except Exception:
            logging.exception("Failed to track event %s", event_type)

    def start_deploy(script: Path) -> bool:
        unit_name = f"vpn-ops-deploy-{int(time.time())}"
        try:
            result = subprocess.run(
                ["systemd-run", "--unit", unit_name, "--collect", str(script)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logging.info("Deploy started via systemd-run unit %s", unit_name)
                return True
            logging.warning(
                "systemd-run failed (rc=%s): %s",
                result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
        except Exception:
            logging.exception("Deploy start via systemd-run failed")
        return False

    async def schedule_deploy_report(bot: Bot) -> None:
        await asyncio.sleep(6)
        await send_deploy_report_if_any(bot, settings, repo)

    async def get_bot_username(bot: Bot) -> str:
        nonlocal bot_username_cache
        if bot_username_cache:
            return bot_username_cache
        me = await bot.get_me()
        bot_username_cache = str(me.username or "").strip()
        return bot_username_cache

    user_lookup_ctx = UserLookupContext(
        repo=repo,
        marzban=marzban,
        build_username=build_username,
        format_expire=format_expire,
        format_limit=format_limit,
        format_used=format_used,
        format_last_online=format_last_online,
        device_label=_device_label,
    )

    broadcast_preview_ctx = BroadcastPreviewContext(
        repo=repo,
        pending_broadcast_format=pending_broadcast_format,
        pending_broadcast_buttons=pending_broadcast_buttons,
        broadcast_format_label=broadcast_format_label,
        broadcast_parse_mode=broadcast_parse_mode,
        broadcast_confirm_keyboard=broadcast_confirm_keyboard,
    )

    async def send_user_lookup(message: Message, target_id: int) -> None:
        await send_user_lookup_impl(
            message=message,
            target_id=target_id,
            ctx=user_lookup_ctx,
        )

    async def send_broadcast_preview(message: Message, body: str, *, admin_id: int | None = None) -> None:
        await send_broadcast_preview_impl(
            message=message,
            body=body,
            admin_id=admin_id,
            ctx=broadcast_preview_ctx,
        )

    async def replace_device_slot(
        *,
        telegram_id: int,
        slot: int,
    ) -> tuple[str, str, dict[str, Any]]:
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

    async def list_replaceable_devices(telegram_id: int) -> list[dict[str, Any]]:
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

    async def guard_message_rate_limit(message: Message) -> bool:
        if not message.from_user:
            return False
        tg_id = int(message.from_user.id)
        try:
            await repo.touch_chat(tg_id)
        except Exception:
            logging.exception("Failed to touch chat %s on message", tg_id)
        if is_admin(tg_id, settings):
            return True
        if message_limiter.allow(f"msg:{tg_id}"):
            return True
        await message.answer("Слишком много запросов. Подождите 10-20 секунд и повторите.")
        return False

    async def guard_callback_rate_limit(callback: CallbackQuery) -> bool:
        if not callback.from_user:
            return False
        tg_id = int(callback.from_user.id)
        try:
            await repo.touch_chat(tg_id)
        except Exception:
            logging.exception("Failed to touch chat %s on callback", tg_id)
        if is_admin(tg_id, settings):
            return True
        if callback_limiter.allow(f"cb:{tg_id}"):
            return True
        await callback.answer("Слишком часто. Подождите немного.", show_alert=True)
        return False

    async def handle_grant_perm(message: Message) -> bool:
        if not message.text:
            return False
        raw = message.text
        if "/grant_perm" not in raw:
            return False
        parts = raw.split()
        cmd_index = None
        for i, part in enumerate(parts):
            if part.startswith("/grant_perm"):
                cmd_index = i
                break
        if cmd_index is None:
            return False
        cmd = parts[cmd_index].split("@", 1)[0]
        if cmd != "/grant_perm":
            return False
        if not await guard_message_rate_limit(message):
            return True
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return True
        args = parts[cmd_index + 1 :]
        if len(args) not in {1, 2}:
            await message.answer("Использование: /grant_perm <telegram_id> [gb]")
            return True
        try:
            target = int(args[0])
            gb = int(args[1]) if len(args) == 2 else 0
        except ValueError:
            await message.answer("Ошибка формата. Пример: /grant_perm 386029735 0")
            return True
        if gb < 0:
            await message.answer("GB должно быть >= 0.")
            return True
        updated = await extend_access_all_devices(
            telegram_id=target,
            days=0,
            gb=gb,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        expire_val = None
        try:
            primary_row = await repo.get_user(target)
            primary_username = (
                str(primary_row["marzban_username"])
                if primary_row
                else build_username(target)
            )
            primary_user = await marzban.get_user(primary_username)
            expire_val = primary_user.get("expire") if primary_user else None
        except Exception:
            logging.exception("grant_perm: failed to read expire after perm grant for %s", target)
        logging.info("grant_perm: perm access for %s, expire=%s", target, expire_val)
        await message.answer("Готово. Бессрочный доступ выдан.")
        await notify_access_updated(
            message.bot,
            target,
            updated,
            "Вам выдан бессрочный доступ.",
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        return True

    async def bind_web_order_to_user(*, telegram_id: int, order_id: str) -> tuple[bool, str]:
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


    register_user_message_handlers(
        router=router,
        deps=UserMessageDeps(
            settings=settings,
            repo=repo,
            guard_message_rate_limit=guard_message_rate_limit,
            extract_start_payload=extract_start_payload,
            parse_referrer_from_payload=parse_referrer_from_payload,
            parse_web_order_from_payload=parse_web_order_from_payload,
            bind_web_order_fn=bind_web_order_to_user,
            build_start_text=build_start_text,
            plan_gb_text=plan_gb_text,
            format_device_limit=format_device_limit,
            keyboard_for_user=keyboard_for_user,
            is_admin_fn=is_admin,
            track_event=track_event,
            bot_token=settings.bot_token,
            enabled_payment_providers=enabled_payment_providers,
            get_bot_username=get_bot_username,
            build_user_faq_text=build_user_faq_text,
            quick_connect_guide_text=quick_connect_guide_text,
            normalize_channel_url=normalize_channel_url,
        ),
    )
    @router.message(F.text.contains("/grant_perm"))
    async def grant_perm_any(message: Message) -> None:
        if await handle_grant_perm(message):
            return








    @router.message(Command("admin"))
    async def admin_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        await message.answer(
            "Админ-кабинет:\n"
            "- Статистика по пользователям и платежам\n"
            "- Быстрые действия без ручного ввода команд",
            reply_markup=admin_panel_keyboard(),
        )

    @router.message(Command("broadcast"))
    async def broadcast_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        text = (message.text or "").split(maxsplit=1)
        if len(text) < 2 or not text[1].strip():
            pending_broadcast_prompt.add(int(message.from_user.id))
            pending_broadcast_format.setdefault(int(message.from_user.id), "plain")
            pending_broadcast_buttons.setdefault(int(message.from_user.id), True)
            await message.answer("Введите текст рассылки или «отмена».")
            return
        body = text[1].strip()
        admin_id = int(message.from_user.id)
        pending_broadcast_text[admin_id] = body
        pending_broadcast_format.setdefault(admin_id, "plain")
        pending_broadcast_buttons.setdefault(admin_id, True)
        await send_broadcast_preview(message, body)

    @router.message(Command("user"))
    async def user_lookup_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Использование: /user <telegram_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("ID должен быть числом. Пример: /user 386029735")
            return
        await send_user_lookup(message, target_id)

    @router.message(Command("config"))
    async def config_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        _, user, _ = await ensure_device(
            telegram_id=int(message.from_user.id),
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=False,
        )
        if not user:
            await message.answer("❗ Профиль не найден. Нажмите «🔑 Получить подписку».")
            return
        await send_status(message, user)
        await send_device_links(
            message=message,
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(Command("diag"))
    async def diag_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        lines: list[str] = [f"🧪 Диагностика\nTG: {tg_id}"]

        devices = await repo.list_devices(tg_id)
        if not devices:
            lines.append("Профиль не найден. Нажмите «🔑 Получить подписку».")
            await message.answer("\n".join(lines))
            return

        lines.append("Устройства:")
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            username = str(row.get("marzban_username") or "").strip()
            mz_user = await marzban.get_user(username) if username else None
            if not mz_user:
                lines.append(f"- {device_id}. {label}: не найдено в Marzban")
                continue
            status = str(mz_user.get("status", "unknown"))
            used = format_used(int(mz_user.get("used_traffic", 0) or 0))
            expire = format_expire(int(mz_user.get("expire", 0) or 0))
            online = format_last_online(
                mz_user.get("online_at") or mz_user.get("last_online") or mz_user.get("last_online_at")
            )
            lines.append(
                f"- {device_id}. {label}: {status}, онлайн: {online}, трафик: {used}, до: {expire}"
            )

        latest_payment = await repo.get_latest_payment(tg_id)
        if latest_payment:
            purpose = str(latest_payment.get("purpose") or "plan")
            provider = str(latest_payment.get("provider") or "")
            status = str(latest_payment.get("status") or "")
            amount = float(latest_payment.get("amount_rub") or 0)
            updated = int(latest_payment.get("updated_at") or 0)
            updated_text = (
                datetime.fromtimestamp(updated, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
                if updated > 0
                else "n/a"
            )
            lines.append(
                f"Последний платеж: {provider}, {purpose}, {amount:.2f} RUB, {status}, {updated_text}"
            )
        else:
            lines.append("Платежи: не найдено")

        lines.append("Если есть проблемы, отправьте «⚠️ Проблема с подключением».")
        await message.answer("\n".join(lines))

    @router.message(Command("buy"))
    async def buy_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            await message.answer(
                "💳 Выберите тариф для продления устройства 1:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=1),
            )
            return
        tg_id = int(message.from_user.id)
        devices = await repo.list_devices(tg_id)
        if not devices:
            await message.answer(
                "💳 Выберите тариф для продления основного ключа (устройство 1):\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=1),
            )
            return
        if len(devices) == 1:
            only_slot = int(devices[0]["device_id"])
            await message.answer(
                f"💳 Выберите тариф для продления устройства {only_slot}:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=only_slot),
            )
            return
        await message.answer(
            "💳 Выберите, что продлить:",
            reply_markup=buy_target_keyboard(devices),
        )

    @router.message(Command("device"))
    async def device_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        row = await repo.get_user(tg_id)
        if not row:
            await message.answer("❗ Сначала получите ссылку подписки.")
            return
        devices = await repo.list_devices(tg_id)
        if settings.device_limit > 0 and len(devices) >= settings.device_limit:
            await message.answer("Лимит устройств уже исчерпан.")
            return
        if not await repo.has_paid_plan_payment(tg_id):
            await message.answer(
                "📱 Доп. устройство доступно только после оплаты основного тарифа.\n"
                "Сначала нажмите «Купить доступ»."
            )
            return
        await message.answer(
            f"📱 Доп. устройство: {settings.device_add_rub:.2f} RUB.\n"
            "Оплата добавляет только новый слот устройства.\n"
            f"Новое устройство получает +{max(0, int(settings.pay_days))} дней доступа.\n"
            "После оплаты устройство появится автоматически.\n"
            "Название можно задать через «Переименовать устройство».",
            reply_markup=device_methods_keyboard(settings),
        )

    @router.message(Command("replace"))
    async def replace_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        devices = await list_replaceable_devices(tg_id)
        if not devices:
            await message.answer("Активные устройства не найдены. Сначала получите подписку.")
            return
        kb = _devices_replace_keyboard(devices)
        await message.answer(
            "Выберите устройство для перевыпуска ссылки.\n"
            "Старая ссылка выбранного устройства будет отключена.",
            reply_markup=kb,
        )

    @router.message(Command("devices"))
    async def devices_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        devices = await repo.list_devices(int(message.from_user.id))
        if not devices:
            await message.answer("Устройства не найдены. Сначала получите подписку.")
            return
        lines: list[str] = []
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            if label.startswith("Устройство"):
                lines.append(f"{device_id}. {label}")
            else:
                lines.append(f"{device_id}. Устройство {device_id} — {label}")
        await message.answer("Ваши устройства:\n" + "\n".join(lines))

    @router.message(Command("device_name"))
    async def device_name_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Использование: /device_name <id> <имя устройства>")
            return
        try:
            device_id = int(parts[1])
        except ValueError:
            await message.answer("ID устройства должен быть числом. Пример: /device_name 2 Мой ноутбук")
            return
        if device_id < 1:
            await message.answer("ID устройства должен быть >= 1")
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await message.answer(f"ID устройства должен быть в диапазоне 1..{settings.device_limit}")
            return
        name = normalize_device_name(parts[2])
        if not name:
            await message.answer("Имя устройства не может быть пустым.")
            return
        row = await repo.get_device(int(message.from_user.id), device_id)
        if not row:
            await message.answer("Устройство не найдено. Сначала получите подписку.")
            return
        await repo.set_device_name(int(message.from_user.id), device_id, name)
        await message.answer(f"✅ Устройство {device_id} теперь называется: {name}")

    @router.message(Command("check"))
    async def check_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            providers = enabled_payment_providers(settings)
            await message.answer(
                "Использование: /check <" + "|".join(providers) + "> <payment_id>"
                if providers
                else "Провайдеры оплаты не настроены."
            )
            return
        provider = parts[1].lower().strip()
        allowed = enabled_payment_providers(settings)
        if provider not in set(allowed):
            if not allowed:
                await message.answer("Провайдеры оплаты не настроены.")
            else:
                await message.answer("Допустимые провайдеры: " + ", ".join(allowed))
            return
        result, updated = await check_and_apply_payment(
            provider=provider,
            external_id=parts[2],
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
            bot=message.bot,
        )
        await message.answer(result)
        if updated:
            await send_status(message, updated)
            await send_device_links(
                message=message,
                telegram_id=int(message.from_user.id),
                repo=repo,
                marzban=marzban,
                settings=settings,
            )

    @router.message(F.text.in_({"🔑 Получить конфиг", "🔑 Получить подписку"}))
    async def get_config(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        await track_event("config_requested", telegram_id=tg_id)
        _, user, created = await ensure_device(
            telegram_id=tg_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=True,
        )
        if created:
            await message.answer(
                f"🎁 Тестовый доступ выдан: {settings.trial_days} день, {plan_gb_text(settings.trial_gb)}."
            )
            await track_event("trial_issued", telegram_id=tg_id)
        await send_status(message, user or {})
        await send_device_links(
            message=message,
            telegram_id=tg_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(F.text == "📊 Мой статус")
    async def status_cmd(message: Message) -> None:
        await config_cmd(message)

    @router.message(F.text == "💳 Купить доступ")
    async def buy_btn(message: Message) -> None:
        await buy_cmd(message)

    @router.message(F.text == "📂 Еще")
    async def more_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(
            "<b>Дополнительные действия</b>\n"
            "Выберите нужный пункт:",
            reply_markup=more_actions_keyboard(),
            parse_mode="HTML",
        )

    @router.message(F.text == "📱 Добавить устройство")
    async def device_btn(message: Message) -> None:
        await device_cmd(message)

    @router.message(F.text == "🔁 Заменить устройство")
    async def replace_btn(message: Message) -> None:
        await replace_cmd(message)

    @router.message(F.text == "✏️ Переименовать устройство")
    async def device_rename_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        devices = await repo.list_devices(int(message.from_user.id))
        if not devices:
            await message.answer("Устройства не найдены. Сначала получите подписку.")
            return
        kb = _devices_rename_keyboard(devices)
        await message.answer("Выберите устройство для переименования:", reply_markup=kb)

    @router.message(F.text == "🎁 Рефералка")
    async def ref_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        username = await get_bot_username(message.bot)
        if not username:
            await message.answer("Не удалось получить username бота. Попробуйте позже.")
            return
        link = f"https://t.me/{username}?start=ref_{tg_id}"
        stats = await repo.get_referral_stats(tg_id)
        await message.answer(
            "🎁 Реферальная программа:\n"
            f"- Бонус за оплаченного друга: +{max(0, settings.referral_bonus_days)} дн.\n"
            f"- Приглашено: {stats['total']}\n"
            f"- Бонус выдан: {stats['rewarded']}\n"
            f"- Ожидают первую оплату: {stats['pending']}\n\n"
            "Ваша ссылка:\n"
            f"{link}"
        )

    @router.message(F.text == "❓ FAQ")
    async def faq_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(build_user_faq_text(), parse_mode="HTML")

    @router.message(F.text == "🆘 Поддержка")
    async def support_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            await track_event("support_opened", telegram_id=tg_id)
        safe_support_text = html.escape(settings.support_text)
        if settings.support_username:
            await message.answer(
                "<b>🆘 Поддержка</b>\n"
                f"{safe_support_text}\n\n"
                f"Контакт: https://t.me/{settings.support_username}",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "<b>🆘 Поддержка</b>\n"
                f"{safe_support_text}\n\n"
                "Контакт поддержки пока не задан администратором.",
                parse_mode="HTML",
            )

    @router.message(F.text == "📢 Наш канал")
    async def channel_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            await track_event("channel_opened", telegram_id=tg_id)
        link = normalize_channel_url(settings.channel_url)
        if link:
            await message.answer(f"<b>📢 Наш канал</b>\n{link}", parse_mode="HTML")
            return
        await message.answer("Канал пока не настроен. Администратор скоро добавит ссылку.")

    @router.message(F.text == "⚠️ Проблема с подключением")
    async def issue_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        pending_issue.add(tg_id)
        await message.answer(
            "Опишите проблему одним сообщением по шаблону:\n"
            "1) Время (дата и время по МСК)\n"
            "2) Устройство и приложение (iOS/Android/Windows + клиент)\n"
            "3) Что именно не работает\n"
            "4) Ошибка/скрин (если есть)\n"
            "5) Пробовали переимпорт/перезапуск\n\n"
            "Напишите «отмена» чтобы выйти."
        )

    @router.message(F.text == "🛠 Админ-кабинет")
    async def admin_btn(message: Message) -> None:
        await admin_cmd(message)

    register_user_callback_handlers(
        router=router,
        deps=UserCallbackDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_callback_rate_limit=guard_callback_rate_limit,
            list_replaceable_devices=list_replaceable_devices,
            get_bot_username=get_bot_username,
            build_user_faq_text=build_user_faq_text,
            normalize_channel_url=normalize_channel_url,
            pending_issue=pending_issue,
            pending_device_rename=pending_device_rename,
            replace_device_slot=replace_device_slot,
            send_status=send_status,
            send_device_links=send_device_links,
            collect_device_links=collect_device_links,
            send_configs_in_chat=send_configs_in_chat,
            render_config_block=_render_config_block,
            plans_list_text=plans_list_text,
            buy_plan_keyboard=buy_plan_keyboard,
            find_plan=find_plan,
            plan_title=plan_title,
            plan_gb_text=plan_gb_text,
            payment_methods_keyboard=payment_methods_keyboard,
            cryptobot_create_invoice=cryptobot_create_invoice,
            yookassa_create_payment=yookassa_create_payment,
            track_event=track_event,
            pay_action_keyboard=pay_action_keyboard,
            next_device_slot=next_device_slot,
            check_and_apply_payment=check_and_apply_payment,
            device_methods_keyboard=device_methods_keyboard,
            devices_replace_keyboard=_devices_replace_keyboard,
            devices_rename_keyboard=_devices_rename_keyboard,
            device_replace_confirm_keyboard=_device_replace_confirm_keyboard,
            device_label=_device_label,
        ),
    )

    register_fallback_handler(
        router=router,
        deps=FallbackDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_message_rate_limit=guard_message_rate_limit,
            pending_user_lookup=pending_user_lookup,
            pending_device_add_prompt=pending_device_add_prompt,
            pending_broadcast_prompt=pending_broadcast_prompt,
            pending_broadcast_text=pending_broadcast_text,
            pending_broadcast_format=pending_broadcast_format,
            pending_broadcast_buttons=pending_broadcast_buttons,
            pending_device_rename=pending_device_rename,
            pending_issue=pending_issue,
            send_user_lookup=send_user_lookup,
            ensure_device=ensure_device,
            send_broadcast_preview=send_broadcast_preview,
            normalize_device_name=normalize_device_name,
            track_event=track_event,
            keyboard_for_user=keyboard_for_user,
            is_admin_fn=is_admin,
        ),
    )

    register_admin_callback_handlers(
        router=router,
        deps=AdminCallbackDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_callback_rate_limit=guard_callback_rate_limit,
            is_admin_fn=is_admin,
            admin_panel_keyboard=admin_panel_keyboard,
            admin_plans_text=admin_plans_text,
            admin_plans_keyboard=admin_plans_keyboard,
            preset_plans=_preset_plans,
            plans_to_json=_plans_to_json,
            update_env_file=update_env_file,
            plan_title=plan_title,
            plan_gb_text=plan_gb_text,
            build_admin_stats_text=build_admin_stats_text,
            build_ops_report_text=build_ops_report_text,
            start_deploy=start_deploy,
            schedule_deploy_report=schedule_deploy_report,
            pending_user_lookup=pending_user_lookup,
            pending_device_add_prompt=pending_device_add_prompt,
            pending_broadcast_prompt=pending_broadcast_prompt,
            pending_broadcast_format=pending_broadcast_format,
            pending_broadcast_buttons=pending_broadcast_buttons,
            pending_broadcast_text=pending_broadcast_text,
            broadcast_next_format=broadcast_next_format,
            send_broadcast_preview=send_broadcast_preview,
            broadcast_parse_mode=broadcast_parse_mode,
            keyboard_for_user=keyboard_for_user,
            build_ref_top_text=build_ref_top_text,
            enabled_payment_providers=enabled_payment_providers,
            build_support_templates_text=build_support_templates_text,
        ),
    )

    register_admin_message_handlers(
        router=router,
        deps=AdminMessageDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_message_rate_limit=guard_message_rate_limit,
            is_admin_fn=is_admin,
            extend_access_all_devices=extend_access_all_devices,
            build_username=build_username,
            notify_access_updated=notify_access_updated,
            extend_access=extend_access,
            ensure_device=ensure_device,
            extend_access_device=extend_access_device,
            send_status_to_bot=send_status_to_bot,
            send_device_links_to_bot=send_device_links_to_bot,
            sync_expire_across_devices=sync_expire_across_devices,
            format_expire=format_expire,
            replace_device_slot=replace_device_slot,
            env_editable_keys=ENV_EDITABLE_KEYS,
            coerce_env_value=coerce_env_value,
            update_env_file=update_env_file,
            start_deploy=start_deploy,
            schedule_deploy_report=schedule_deploy_report,
            broadcast_menu_update=broadcast_menu_update,
            build_admin_stats_text=build_admin_stats_text,
            build_ref_top_text=build_ref_top_text,
            build_ops_report_text=build_ops_report_text,
            extend_access_days_only=extend_access_days_only,
        ),
    )

    return router


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    settings = Settings.load()
    logging.info(
        "Runtime network settings: iface=%s, port_speed_mbps=%.0f",
        settings.net_iface,
        settings.port_speed_mbps,
    )
    repo = Repo(settings.db_path)
    await repo.open()
    marzban = MarzbanClient(settings)
    bot = Bot(token=settings.bot_token)
    await send_deploy_report_if_any(bot, settings, repo)
    dp = Dispatcher()
    dp.include_router(build_router(settings, repo, marzban))
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        cryptobot_auto_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    yookassa_task = asyncio.create_task(
        yookassa_auto_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    report_task = asyncio.create_task(
        daily_ops_report_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    deploy_report_task = asyncio.create_task(
        deploy_report_worker(
            settings=settings,
            repo=repo,
            bot=bot,
            stop_event=stop_event,
        )
    )
    renewal_task = asyncio.create_task(
        subscription_renewal_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    sub_migration_task = asyncio.create_task(
        subscription_migration_worker(
            settings=settings,
            repo=repo,
            bot=bot,
            stop_event=stop_event,
        )
    )

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        worker_task.cancel()
        yookassa_task.cancel()
        report_task.cancel()
        deploy_report_task.cancel()
        renewal_task.cancel()
        sub_migration_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        try:
            await yookassa_task
        except asyncio.CancelledError:
            pass
        try:
            await report_task
        except asyncio.CancelledError:
            pass
        try:
            await deploy_report_task
        except asyncio.CancelledError:
            pass
        try:
            await renewal_task
        except asyncio.CancelledError:
            pass
        try:
            await sub_migration_task
        except asyncio.CancelledError:
            pass
        await marzban.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())








