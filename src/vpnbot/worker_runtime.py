"""Runtime glue for scheduled background workers and the daily ops report.

The pure scheduling/business logic lives in `bot_workers`. This module
binds those generic loops to the concrete bot-runtime side effects:
admin notifications, payment helpers, formatters, keyboards, messaging.

Kept separate from `bot_runtime` so that the dispatcher wiring stays
focused on aiogram/router configuration.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot

from config import Settings, _normalize_plan_key
from models import Plan
from src.vpnbot.bot_formatters import (
    format_expire,
    format_time_left,
    plan_title,
)
from src.vpnbot.bot_ops import (
    build_admin_stats_text,
    build_ops_report_text,
    build_payments_summary,
)
from src.vpnbot.bot_workers import (
    auto_renew_plan as _auto_renew_plan,
    auto_renew_provider as _auto_renew_provider,
    cryptobot_auto_worker as _cryptobot_auto_worker,
    subscription_migration_worker as _subscription_migration_worker,
    subscription_renewal_worker as _subscription_renewal_worker,
    yookassa_auto_worker as _yookassa_auto_worker,
)
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.device_utils import _device_label
from src.vpnbot.keyboards.bot_keyboards import (
    pay_action_keyboard,
    renewal_actions_keyboard,
)
from src.vpnbot.message_utils import split_message
from src.vpnbot.marzban_sync import audit_marzban_sync
from src.vpnbot.messaging import notify_access_updated
from src.vpnbot.notifications import (
    notify_admin_requeued_processing,
    notify_admin_worker_alert,
)
from src.vpnbot.payment_helpers import (
    apply_paid_payment,
    cryptobot_check_invoice,
    cryptobot_create_invoice,
    yookassa_check_payment,
    yookassa_create_payment,
)
from src.vpnbot.services.bot_marzban import MarzbanClient


def find_plan(settings: Settings, key: str) -> Plan | None:
    normalized = _normalize_plan_key(key, settings.pay_days)
    for plan in settings.plans:
        if plan.key == normalized:
            return plan
    return None


def auto_renew_plan(settings: Settings) -> Plan:
    return _auto_renew_plan(settings, find_plan_fn=find_plan)


def auto_renew_provider(settings: Settings) -> str | None:
    return _auto_renew_provider(settings)


async def send_daily_report(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
) -> None:
    try:
        ops_text = await asyncio.wait_for(
            # `Settings` is structurally compatible with the read-only
            # subset of `SettingsLike` that `build_ops_report_text` actually
            # uses; mypy's invariance check on Protocol attrs is too strict.
            build_ops_report_text(settings, marzban, sar_seconds=60),  # type: ignore[arg-type]
            timeout=75,
        )
    except Exception:
        logging.exception("Daily report: ops text failed")
        ops_text = "Ops отчет: ошибка формирования"
    try:
        payments_text = await asyncio.wait_for(build_payments_summary(repo), timeout=5)
    except Exception:
        logging.exception("Daily report: payments text failed")
        payments_text = "Платежи: ошибка формирования"
    try:
        stats_text = await asyncio.wait_for(build_admin_stats_text(repo, marzban), timeout=20)
    except Exception:
        logging.exception("Daily report: stats text failed")
        stats_text = "Статистика: ошибка формирования"
    header = f"📅 Ежедневный отчет ({datetime.now().strftime('%d.%m.%Y %H:%M')})"
    full = f"{header}\n\n{ops_text}\n\n{payments_text}\n\n{stats_text}"
    for chunk in split_message(full, limit=3500):
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(int(admin_id), chunk)
            except Exception:
                logging.exception("Daily report: failed to send to admin %s", admin_id)


async def daily_ops_report_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.ops_report_enabled:
        logging.info("Daily ops report worker disabled")
        return
    logging.info(
        "Daily ops report worker started: time=%02d:%02d",
        settings.ops_report_hour,
        settings.ops_report_minute,
    )
    last_sent: Any = None
    while not stop_event.is_set():
        now = datetime.now()
        target = now.replace(
            hour=settings.ops_report_hour,
            minute=settings.ops_report_minute,
            second=0,
            microsecond=0,
        )
        if now >= target and (last_sent is None or last_sent != now.date()):
            try:
                await send_daily_report(settings=settings, repo=repo, marzban=marzban, bot=bot)
                last_sent = now.date()
            except Exception:
                logging.exception("Daily report: failed")
            target = target + timedelta(days=1)
        elif now >= target:
            target = target + timedelta(days=1)
        wait_seconds = max(30, int((target - now).total_seconds()))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            continue


async def cryptobot_auto_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    logging.info(
        "Crypto payment worker started: enabled=%s interval_sec=%s",
        settings.cryptobot_enabled(),
        max(10, settings.cryptobot_poll_seconds),
    )
    await _cryptobot_auto_worker(
        settings=settings,
        repo=repo,
        marzban=marzban,
        bot=bot,
        stop_event=stop_event,
        notify_admin_requeued_processing_fn=notify_admin_requeued_processing,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
        cryptobot_check_invoice_fn=cryptobot_check_invoice,
        apply_paid_payment_fn=apply_paid_payment,
        notify_access_updated_fn=notify_access_updated,
    )


async def yookassa_auto_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    logging.info(
        "YooKassa payment worker started: enabled=%s interval_sec=%s",
        settings.yookassa_enabled(),
        max(10, settings.yookassa_poll_seconds),
    )
    await _yookassa_auto_worker(
        settings=settings,
        repo=repo,
        marzban=marzban,
        bot=bot,
        stop_event=stop_event,
        notify_admin_requeued_processing_fn=notify_admin_requeued_processing,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
        yookassa_check_payment_fn=yookassa_check_payment,
        apply_paid_payment_fn=apply_paid_payment,
        notify_access_updated_fn=notify_access_updated,
    )


async def subscription_renewal_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if (
        not settings.renewal_alerts_enabled
        and not settings.renewal_expired_alert_enabled
        and not settings.auto_renew_invoice_enabled
    ):
        logging.info("Subscription renewal worker disabled")
    else:
        logging.info(
            "Subscription renewal worker started: reminders=%s expired=%s auto_invoice=%s interval_sec=%s",
            settings.renewal_alerts_enabled,
            settings.renewal_expired_alert_enabled,
            settings.auto_renew_invoice_enabled,
            max(60, settings.renewal_alert_interval_sec),
        )
    await _subscription_renewal_worker(
        settings=settings,
        repo=repo,
        marzban=marzban,
        bot=bot,
        stop_event=stop_event,
        auto_renew_provider_fn=auto_renew_provider,
        auto_renew_plan_fn=auto_renew_plan,
        device_label_fn=_device_label,
        format_expire_fn=format_expire,
        format_time_left_fn=format_time_left,
        renewal_actions_keyboard_fn=renewal_actions_keyboard,
        plan_title_fn=plan_title,
        yookassa_create_payment_fn=yookassa_create_payment,
        cryptobot_create_invoice_fn=cryptobot_create_invoice,
        pay_action_keyboard_fn=pay_action_keyboard,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
    )


async def subscription_migration_worker(
    *,
    settings: Settings,
    repo: Repo,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.sub_migration_reminder_enabled or settings.config_delivery_mode == "direct":
        logging.info(
            "Subscription migration worker disabled: enabled=%s delivery_mode=%s",
            settings.sub_migration_reminder_enabled,
            settings.config_delivery_mode,
        )
    else:
        logging.info(
            "Subscription migration worker started: interval_sec=%s lookback_days=%s batch=%s",
            max(300, int(settings.sub_migration_reminder_interval_sec)),
            max(1, int(settings.sub_migration_reminder_lookback_days)),
            max(1, min(200, int(settings.sub_migration_reminder_batch))),
        )
    await _subscription_migration_worker(
        settings=settings,
        repo=repo,
        bot=bot,
        stop_event=stop_event,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
    )


async def marzban_sync_audit_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.marzban_sync_audit_enabled:
        logging.info("Marzban sync audit worker disabled")
        return

    interval = max(900, int(settings.marzban_sync_audit_interval_sec))
    include_noncritical = bool(settings.marzban_sync_audit_alert_noncritical)
    logging.info(
        "Marzban sync audit worker started: interval_sec=%s alert_noncritical=%s",
        interval,
        include_noncritical,
    )

    while not stop_event.is_set():
        try:
            report = await audit_marzban_sync(
                repo,
                marzban,
                limit=max(20, int(settings.marzban_sync_audit_limit)),
            )
            if report.has_findings(include_noncritical=include_noncritical):
                await notify_admin_worker_alert(
                    bot=bot,
                    settings=settings,
                    key="worker.marzban_sync.findings",
                    title="Marzban/DB sync findings",
                    details=report.summary_text(
                        show=max(1, int(settings.marzban_sync_audit_show)),
                        include_noncritical=include_noncritical,
                    ),
                )
        except Exception as exc:
            logging.exception("Marzban sync audit worker iteration failed")
            try:
                await notify_admin_worker_alert(
                    bot=bot,
                    settings=settings,
                    key="worker.marzban_sync.iteration_failed",
                    title="Marzban/DB sync audit failed",
                    details=str(exc),
                )
            except Exception:
                logging.exception("Marzban sync audit worker: alert notify failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
