
import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Any
from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    CallbackQuery,
    Message,
)
from app_texts import (
    build_start_text,
    build_support_templates_text,
    build_user_faq_text,
)
from src.vpnbot.payment_helpers import (
    apply_paid_payment,
    cryptobot_check_invoice,
    cryptobot_create_invoice,
    yookassa_check_payment,
    yookassa_create_payment,
)
from config import (
    _plans_to_json,
    _preset_plans,
    Settings,
)
from src.vpnbot.services.payment_flow import (
    check_and_apply_payment as pf_check_and_apply_payment,
)
from src.vpnbot.bot_formatters import (
    admin_plans_text,
    format_expire,
    format_last_online,
    format_limit,
    format_used,
    plan_gb_text,
    plan_title,
    plans_list_text,
)
from src.vpnbot.device_utils import (
    _device_label,
    format_device_limit,
    next_device_slot,
    normalize_device_name,
)
from src.vpnbot.env_utils import (
    ENV_EDITABLE_KEYS,
    coerce_env_value,
    normalize_channel_url,
    update_env_file,
)
from src.vpnbot.message_utils import (
    quick_connect_guide_text,
)
from src.vpnbot.messaging import (
    _render_config_block,
    collect_device_links,
    notify_access_updated,
    send_configs_in_chat,
    send_device_links,
    send_device_links_to_bot,
    send_status,
    send_status_to_bot,
)
from src.vpnbot.permissions import is_admin
from src.vpnbot.deploy_reports import (
    broadcast_menu_update,
    deploy_report_worker,
    send_deploy_report_if_any,
)
from src.vpnbot.worker_runtime import (
    cryptobot_auto_worker,
    daily_ops_report_worker,
    find_plan,
    marzban_sync_audit_worker,
    olcrtc_rescue_watchdog_worker,
    subscription_migration_worker,
    subscription_renewal_worker,
    xray_quality_monitor_worker,
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
from src.vpnbot.handlers.bot_handlers_user_runtime import (
    UserRuntimeDeps,
    register_user_runtime_handlers,
)
from src.vpnbot.handlers.bot_handlers_admin_runtime import (
    AdminRuntimeDeps,
    register_admin_runtime_handlers,
)
from src.vpnbot.runtime_helpers import (
    bind_web_order_to_user as bind_web_order_to_user_impl,
    list_replaceable_devices as list_replaceable_devices_impl,
    replace_device_slot as replace_device_slot_impl,
)
from src.vpnbot.keyboards.bot_keyboards import (
    admin_panel_keyboard,
    admin_plans_keyboard,
    broadcast_confirm_keyboard,
    broadcast_format_label,
    broadcast_next_format,
    broadcast_parse_mode,
    buy_plan_keyboard,
    device_methods_keyboard,
    device_replace_confirm_keyboard,
    devices_rename_keyboard,
    devices_replace_keyboard,
    keyboard_for_user,
    pay_action_keyboard,
    payment_methods_keyboard,
)
from src.vpnbot.bot_access import (
    ensure_device,
    extend_access,
    extend_access_all_devices,
    extend_access_days_only,
    extend_access_device,
    sync_expire_across_devices,
)
from src.vpnbot.services.bot_marzban import MarzbanClient
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
    build_username,
    extract_start_payload,
    parse_referrer_from_payload,
    parse_web_order_from_payload,
)

def enabled_payment_providers(settings: Settings) -> list[str]:
    providers: list[str] = []
    if settings.cryptobot_enabled():
        providers.append("crypto")
    if settings.yookassa_enabled():
        providers.append("card")
    return providers


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

    async def send_user_lookup(message: Message, target_id: int | str) -> None:
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
        return await replace_device_slot_impl(
            telegram_id=telegram_id,
            slot=slot,
            repo=repo,
            marzban=marzban,
        )

    async def list_replaceable_devices(telegram_id: int) -> list[dict[str, Any]]:
        return await list_replaceable_devices_impl(
            telegram_id,
            repo=repo,
            marzban=marzban,
        )

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
        return await bind_web_order_to_user_impl(
            telegram_id=telegram_id,
            order_id=order_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
            track_event=track_event,
        )

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

    register_user_runtime_handlers(
        router=router,
        deps=UserRuntimeDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_message_rate_limit=guard_message_rate_limit,
            list_replaceable_devices=list_replaceable_devices,
            get_bot_username=get_bot_username,
            track_event=track_event,
            pending_issue=pending_issue,
            check_and_apply_payment=check_and_apply_payment,
            enabled_payment_providers=enabled_payment_providers,
        ),
    )

    register_admin_runtime_handlers(
        router=router,
        deps=AdminRuntimeDeps(
            settings=settings,
            repo=repo,
            guard_message_rate_limit=guard_message_rate_limit,
            handle_grant_perm=handle_grant_perm,
            send_broadcast_preview=send_broadcast_preview,
            send_user_lookup=send_user_lookup,
            pending_broadcast_prompt=pending_broadcast_prompt,
            pending_broadcast_format=pending_broadcast_format,
            pending_broadcast_buttons=pending_broadcast_buttons,
            pending_broadcast_text=pending_broadcast_text,
            track_event=track_event,
        ),
    )

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
            devices_replace_keyboard=devices_replace_keyboard,
            devices_rename_keyboard=devices_rename_keyboard,
            device_replace_confirm_keyboard=device_replace_confirm_keyboard,
            device_label=_device_label,
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
    marzban_sync_task = asyncio.create_task(
        marzban_sync_audit_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    xray_quality_task = asyncio.create_task(
        xray_quality_monitor_worker(
            settings=settings,
            bot=bot,
            stop_event=stop_event,
        )
    )
    olcrtc_rescue_watchdog_task = asyncio.create_task(
        olcrtc_rescue_watchdog_worker(
            settings=settings,
            repo=repo,
            bot=bot,
            stop_event=stop_event,
        )
    )

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        stop_event.set()
        worker_task.cancel()
        yookassa_task.cancel()
        report_task.cancel()
        deploy_report_task.cancel()
        renewal_task.cancel()
        sub_migration_task.cancel()
        marzban_sync_task.cancel()
        xray_quality_task.cancel()
        olcrtc_rescue_watchdog_task.cancel()
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
        try:
            await marzban_sync_task
        except asyncio.CancelledError:
            pass
        try:
            await xray_quality_task
        except asyncio.CancelledError:
            pass
        try:
            await olcrtc_rescue_watchdog_task
        except asyncio.CancelledError:
            pass
        await marzban.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())








