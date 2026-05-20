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
from src.vpnbot.olcrtc_rescue import (
    active_rescue_session_ids,
    build_deploy_steps,
    build_rescue_uri_for_room,
    build_rescue_user_message,
    create_local_session,
    default_client_id,
    fetch_rescue_list,
    format_rescue_watchdog_alert,
    parse_rescue_list_output,
    parse_room_broker_output,
    rescue_assigned_replacement_candidates,
    rescue_findings_with_stale_pool_rows,
    rescue_pool_warm_candidates,
    rescue_room_broker_request_count,
    rescue_restartable_session_ids,
    rescue_stale_pool_rows,
    restart_rescue_session,
    rescue_watchdog_findings,
    run_room_broker,
    run_steps_async,
    stop_rescue_session,
    wait_for_rescue_session_active,
)
from src.vpnbot.payment_helpers import (
    apply_paid_payment,
    cryptobot_check_invoice,
    cryptobot_create_invoice,
    yookassa_check_payment,
    yookassa_create_payment,
)
from src.vpnbot.services.bot_marzban import MarzbanClient
from src.vpnbot.xray_quality import format_xray_quality_report, summarize_xray_error_log


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


async def xray_quality_monitor_worker(
    *,
    settings: Settings,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.xray_quality_monitor_enabled:
        logging.info("Xray quality monitor worker disabled")
        return

    interval = max(300, int(settings.xray_quality_monitor_interval_sec))
    window_min = max(1, int(settings.xray_quality_monitor_window_min))
    threshold = max(1, int(settings.xray_quality_monitor_threshold))
    logging.info(
        "Xray quality monitor worker started: path=%s window_min=%s threshold=%s interval_sec=%s",
        settings.xray_error_log_path,
        window_min,
        threshold,
        interval,
    )

    while not stop_event.is_set():
        try:
            summary = summarize_xray_error_log(
                settings.xray_error_log_path,
                window_minutes=window_min,
            )
            if summary.read_error or summary.file_missing or summary.has_problem(threshold=threshold):
                await notify_admin_worker_alert(
                    bot=bot,
                    settings=settings,
                    key="worker.xray_quality.findings",
                    title="Xray quality findings",
                    details=format_xray_quality_report(
                        summary,
                        show=max(1, int(settings.xray_quality_monitor_show)),
                    ),
                )
        except Exception as exc:
            logging.exception("Xray quality monitor worker iteration failed")
            try:
                await notify_admin_worker_alert(
                    bot=bot,
                    settings=settings,
                    key="worker.xray_quality.iteration_failed",
                    title="Xray quality monitor failed",
                    details=str(exc),
                )
            except Exception:
                logging.exception("Xray quality monitor worker: alert notify failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def olcrtc_rescue_watchdog_worker(
    *,
    settings: Settings,
    repo: Any | None = None,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.olcrtc_rescue_watchdog_enabled:
        logging.info("olcRTC Rescue watchdog disabled")
        return

    deploy_host = str(settings.olcrtc_rescue_deploy_host or "").strip()
    if not deploy_host:
        logging.info("olcRTC Rescue watchdog disabled: deploy host is empty")
        return

    interval = max(300, int(settings.olcrtc_rescue_watchdog_interval_sec))
    logging.info(
        "olcRTC Rescue watchdog started: host=%s remote_root=%s interval_sec=%s",
        deploy_host,
        settings.olcrtc_rescue_remote_root,
        interval,
    )

    while not stop_event.is_set():
        try:
            result = await fetch_rescue_list(
                deploy_host=deploy_host,
                remote_root=settings.olcrtc_rescue_remote_root,
                timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
            )
            if not result.ok:
                await notify_admin_worker_alert(
                    bot=bot,
                    settings=settings,
                    key="worker.olcrtc_rescue.list_failed",
                    title="olcRTC Rescue watchdog failed",
                    details=f"failed_step={result.failed_step}\n{result.output}",
                )
            else:
                remote_sessions = parse_rescue_list_output(result.output)
                rooms = await repo.list_rescue_rooms() if repo is not None else []
                pool_warm_problem_details: list[str] = []
                if settings.olcrtc_rescue_pool_auto_warm and repo is not None:
                    broker_count = rescue_room_broker_request_count(
                        rooms,
                        remote_sessions,
                        min_warm=settings.olcrtc_rescue_pool_min_warm,
                        min_free=settings.olcrtc_rescue_pool_min_free,
                        max_rooms=settings.olcrtc_rescue_room_broker_max_rooms_per_tick,
                    )
                    if (
                        broker_count > 0
                        and settings.olcrtc_rescue_room_broker_enabled
                        and settings.olcrtc_rescue_room_broker_command
                    ):
                        broker_result = await run_room_broker(
                            command_template=settings.olcrtc_rescue_room_broker_command,
                            count=broker_count,
                            timeout_sec=settings.olcrtc_rescue_room_broker_timeout_sec,
                        )
                        if broker_result.ok:
                            urls = parse_room_broker_output(broker_result.output)
                            for room_url in urls:
                                room_id = room_url.rsplit("/", 1)[-1]
                                await repo.add_rescue_room(
                                    room_id=room_id,
                                    room_url=room_url,
                                    note="auto-created by room broker",
                                )
                            logging.info(
                                f"room_broker: ok requested={broker_count} added={len(urls)}\n{broker_result.output}"
                            )
                            rooms = await repo.list_rescue_rooms()
                        else:
                            pool_warm_problem_details.append(
                                f"room_broker: failed at {broker_result.failed_step}\n{broker_result.output}"
                            )

                    candidates = rescue_pool_warm_candidates(
                        rooms,
                        remote_sessions,
                        min_warm=settings.olcrtc_rescue_pool_min_warm,
                        max_to_warm=settings.olcrtc_rescue_pool_max_warm_per_tick,
                    )
                    for room in candidates:
                        room_id = str(room["room_id"])
                        try:
                            session = create_local_session(room=str(room["room_url"]), client_id="olcbox")
                            steps = build_deploy_steps(
                                session_id=session.session_id,
                                local_dir=session.out_dir,
                                deploy_host=deploy_host,
                                remote_root=settings.olcrtc_rescue_remote_root,
                                install_service=settings.olcrtc_rescue_install_service,
                                start_service=True,
                                safe_ssh=True,
                            )
                            warm_result = await run_steps_async(
                                steps,
                                timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                            )
                            if warm_result.ok:
                                is_active, active_check_output = await wait_for_rescue_session_active(
                                    session_id=session.session_id,
                                    deploy_host=deploy_host,
                                    remote_root=settings.olcrtc_rescue_remote_root,
                                    timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                                )
                                if not is_active:
                                    await repo.mark_rescue_room_status(
                                        room_id=room_id,
                                        status="bad",
                                        session_id=session.session_id,
                                        increment_fail_count=True,
                                    )
                                    await stop_rescue_session(
                                        session_id=session.session_id,
                                        deploy_host=deploy_host,
                                        timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                                    )
                                    pool_warm_problem_details.append(
                                        f"auto_warm {room_id}: relay did not become active; marked bad "
                                        f"session={session.session_id}\n{active_check_output}"
                                    )
                                    continue
                                await repo.mark_rescue_room_warm(
                                    room_id=room_id,
                                    session_id=session.session_id,
                                    key_hex=session.key_hex,
                                    client_id=session.client_id,
                                    uri=session.uri,
                                )
                                logging.info(
                                    f"auto_warm {room_id}: ok session={session.session_id}\n{warm_result.output}"
                                )
                            else:
                                await repo.mark_rescue_room_status(
                                    room_id=room_id,
                                    status="free",
                                    increment_fail_count=True,
                                )
                                pool_warm_problem_details.append(
                                    f"auto_warm {room_id}: failed at {warm_result.failed_step}\n{warm_result.output}"
                                )
                        except Exception as exc:
                            logging.exception("olcRTC Rescue pool auto-warm failed for room=%s", room_id)
                            try:
                                await repo.mark_rescue_room_status(
                                    room_id=room_id,
                                    status="free",
                                    increment_fail_count=True,
                                )
                            except Exception:
                                logging.exception("olcRTC Rescue pool auto-warm rollback failed for room=%s", room_id)
                            pool_warm_problem_details.append(f"auto_warm {room_id}: crashed: {exc}")

                if pool_warm_problem_details:
                    await notify_admin_worker_alert(
                        bot=bot,
                        settings=settings,
                        key="worker.olcrtc_rescue.pool_auto_warm",
                        title="olcRTC Rescue pool auto-warm",
                        details="\n\n".join(pool_warm_problem_details),
                    )

                stale_rows: list[dict[str, Any]] = []
                stale_warm_session_ids: set[str] = set()
                if repo is not None:
                    stale_rows = rescue_stale_pool_rows(
                        rooms,
                        remote_sessions,
                        min_non_active_age_sec=settings.olcrtc_rescue_assigned_replace_min_age_sec,
                    )
                    for stale_room in stale_rows:
                        if str(stale_room.get("status") or "") != "warm":
                            continue
                        stale_session_id = str(stale_room.get("session_id") or "").strip()
                        if stale_session_id:
                            stale_warm_session_ids.add(stale_session_id)
                        await repo.mark_rescue_room_status(
                            room_id=str(stale_room["room_id"]),
                            status="bad",
                            session_id=stale_session_id,
                            increment_fail_count=True,
                        )
                    if stale_warm_session_ids:
                        logging.info(
                            "olcRTC Rescue watchdog marked stale warm rooms bad: %s",
                            ", ".join(sorted(stale_warm_session_ids)),
                        )
                        rooms = await repo.list_rescue_rooms()

                findings = rescue_watchdog_findings(result.output)
                assigned_stale_rows = [
                    row for row in stale_rows if str(row.get("status") or "") == "assigned"
                ]
                findings = rescue_findings_with_stale_pool_rows(
                    findings,
                    assigned_stale_rows,
                    remote_sessions,
                )
                if findings:
                    replaced_session_ids: set[str] = set()
                    replacement_problem_details: list[str] = []
                    recovery_details: list[str] = []
                    if settings.olcrtc_rescue_assigned_auto_replace and repo is not None:
                        for old_room in rescue_assigned_replacement_candidates(
                            rooms,
                            findings,
                            max_to_replace=settings.olcrtc_rescue_assigned_max_replace_per_tick,
                            min_non_active_age_sec=settings.olcrtc_rescue_assigned_replace_min_age_sec,
                        ):
                            old_room_id = str(old_room["room_id"])
                            old_session_id = str(old_room.get("session_id") or "")
                            target_tg_id = int(old_room["assigned_tg_id"])
                            claimed = await repo.claim_next_free_rescue_room(
                                telegram_id=target_tg_id,
                                active_warm_session_ids=active_rescue_session_ids(remote_sessions),
                            )
                            if claimed is None:
                                replacement_problem_details.append(
                                    f"auto_replace {old_session_id}: no warm/free room available"
                                )
                                continue

                            claimed_room_id = str(claimed["room_id"])
                            try:
                                claimed_from_status = str(claimed.get("claimed_from_status") or "")
                                if claimed_from_status == "warm":
                                    key_hex = str(claimed.get("key_hex") or "").strip()
                                    new_session_id = str(claimed.get("session_id") or "").strip()
                                    if not key_hex or not new_session_id:
                                        await repo.mark_rescue_room_status(
                                            room_id=claimed_room_id,
                                            status="free",
                                            increment_fail_count=True,
                                        )
                                        replacement_problem_details.append(
                                            f"auto_replace {old_session_id}: warm replacement has no key/session"
                                        )
                                        continue
                                    client_id = default_client_id(tg_id=str(target_tg_id))
                                    uri = build_rescue_uri_for_room(
                                        room=str(claimed["room_url"]),
                                        key_hex=key_hex,
                                        client_id=client_id,
                                    )
                                    await repo.mark_rescue_room_assigned(
                                        room_id=claimed_room_id,
                                        telegram_id=target_tg_id,
                                        session_id=new_session_id,
                                        key_hex=key_hex,
                                        client_id=client_id,
                                        uri=uri,
                                    )
                                else:
                                    new_session = create_local_session(
                                        room=str(claimed["room_url"]),
                                        tg_id=str(target_tg_id),
                                    )
                                    steps = build_deploy_steps(
                                        session_id=new_session.session_id,
                                        local_dir=new_session.out_dir,
                                        deploy_host=deploy_host,
                                        remote_root=settings.olcrtc_rescue_remote_root,
                                        install_service=settings.olcrtc_rescue_install_service,
                                        start_service=True,
                                        safe_ssh=True,
                                    )
                                    deploy_result = await run_steps_async(
                                        steps,
                                        timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                                    )
                                    if not deploy_result.ok:
                                        await repo.mark_rescue_room_status(
                                            room_id=claimed_room_id,
                                            status="free",
                                            increment_fail_count=True,
                                        )
                                        replacement_problem_details.append(
                                            f"auto_replace {old_session_id}: deploy failed at "
                                            f"{deploy_result.failed_step}\n{deploy_result.output}"
                                        )
                                        continue
                                    new_session_id = new_session.session_id
                                    uri = new_session.uri
                                    await repo.mark_rescue_room_assigned(
                                        room_id=claimed_room_id,
                                        telegram_id=target_tg_id,
                                        session_id=new_session.session_id,
                                        key_hex=new_session.key_hex,
                                        client_id=new_session.client_id,
                                        uri=new_session.uri,
                                    )

                                await bot.send_message(
                                    target_tg_id,
                                    "RootVPN Rescue-сессия автоматически заменена: "
                                    "старая комната отвалилась.\n\n"
                                    + build_rescue_user_message(uri),
                                )
                                await repo.mark_rescue_room_status(
                                    room_id=old_room_id,
                                    status="bad",
                                    session_id=old_session_id,
                                    telegram_id=target_tg_id,
                                    increment_fail_count=True,
                                )
                                if old_session_id:
                                    await stop_rescue_session(
                                        session_id=old_session_id,
                                        deploy_host=deploy_host,
                                        timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                                    )
                                replaced_session_ids.add(old_session_id)
                                recovery_details.append(
                                    f"auto_replace {old_session_id}: ok new_session={new_session_id} "
                                    f"room={claimed['room_url']} tg={target_tg_id}"
                                )
                            except Exception as exc:
                                logging.exception("olcRTC Rescue assigned auto-replace failed for %s", old_session_id)
                                try:
                                    await repo.mark_rescue_room_status(
                                        room_id=claimed_room_id,
                                        status="free",
                                        increment_fail_count=True,
                                    )
                                except Exception:
                                    logging.exception(
                                        "olcRTC Rescue assigned auto-replace rollback failed for %s",
                                        claimed_room_id,
                                    )
                                replacement_problem_details.append(f"auto_replace {old_session_id}: crashed: {exc}")

                    restart_details: list[str] = []
                    cleanup_problem_details: list[str] = []
                    if settings.olcrtc_rescue_watchdog_auto_restart:
                        restart_rooms = await repo.list_rescue_rooms() if repo is not None else rooms
                        restartable_session_ids = rescue_restartable_session_ids(restart_rooms)
                        for session in findings[:3]:
                            if session.session_id in replaced_session_ids:
                                continue
                            if repo is not None and session.session_id not in restartable_session_ids:
                                cleanup_result = await stop_rescue_session(
                                    session_id=session.session_id,
                                    deploy_host=deploy_host,
                                    timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                                )
                                if cleanup_result.ok:
                                    recovery_details.append(
                                        f"auto_cleanup {session.session_id}: stopped orphan non-active unit"
                                    )
                                else:
                                    cleanup_problem_details.append(
                                        f"auto_cleanup {session.session_id}: failed at "
                                        f"{cleanup_result.failed_step}\n{cleanup_result.output}"
                                    )
                                continue
                            restart_result = await restart_rescue_session(
                                session_id=session.session_id,
                                deploy_host=deploy_host,
                                timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                            )
                            status = "ok" if restart_result.ok else f"failed at {restart_result.failed_step}"
                            restart_details.append(
                                f"auto_restart {session.session_id}: {status}\n{restart_result.output}"
                            )
                    followup_findings = findings
                    if replaced_session_ids or restart_details or recovery_details or cleanup_problem_details:
                        followup = await fetch_rescue_list(
                            deploy_host=deploy_host,
                            remote_root=settings.olcrtc_rescue_remote_root,
                            timeout_sec=max(5, int(settings.olcrtc_rescue_deploy_timeout_sec)),
                        )
                        if followup.ok:
                            followup_remote_sessions = parse_rescue_list_output(followup.output)
                            followup_rooms = await repo.list_rescue_rooms() if repo is not None else []
                            followup_stale_rows = (
                                rescue_stale_pool_rows(
                                    followup_rooms,
                                    followup_remote_sessions,
                                    min_non_active_age_sec=settings.olcrtc_rescue_assigned_replace_min_age_sec,
                                )
                                if repo is not None
                                else []
                            )
                            followup_findings = rescue_findings_with_stale_pool_rows(
                                rescue_watchdog_findings(followup.output),
                                [
                                    row
                                    for row in followup_stale_rows
                                    if str(row.get("status") or "") == "assigned"
                                ],
                                followup_remote_sessions,
                            )
                        else:
                            cleanup_problem_details.append(
                                f"post-recovery list failed at {followup.failed_step}\n{followup.output}"
                            )

                    if followup_findings or cleanup_problem_details or replacement_problem_details:
                        details = format_rescue_watchdog_alert(followup_findings, deploy_host=deploy_host)
                        extra_details = [
                            *cleanup_problem_details,
                            *replacement_problem_details,
                            *restart_details,
                        ]
                        if extra_details:
                            details += "\n\n" + "\n\n".join(extra_details)
                        await notify_admin_worker_alert(
                            bot=bot,
                            settings=settings,
                            key="worker.olcrtc_rescue.findings",
                            title="olcRTC Rescue sessions need attention",
                            details=details,
                        )
                    elif recovery_details:
                        logging.info(
                            "olcRTC Rescue watchdog recovered sessions without admin alert: %s",
                            " | ".join(recovery_details),
                        )
        except Exception as exc:
            logging.exception("olcRTC Rescue watchdog iteration failed")
            try:
                await notify_admin_worker_alert(
                    bot=bot,
                    settings=settings,
                    key="worker.olcrtc_rescue.iteration_failed",
                    title="olcRTC Rescue watchdog crashed",
                    details=str(exc),
                )
            except Exception:
                logging.exception("olcRTC Rescue watchdog: alert notify failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
