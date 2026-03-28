from __future__ import annotations

import asyncio
import logging
import time
from typing import Any


def auto_renew_plan(settings: Any, *, find_plan_fn: Any) -> Any:
    if settings.auto_renew_invoice_plan_key:
        selected = find_plan_fn(settings, settings.auto_renew_invoice_plan_key)
        if selected is not None:
            return selected
    return settings.plans[0]


def auto_renew_provider(settings: Any) -> str | None:
    preferred = settings.auto_renew_invoice_provider
    if preferred == "card" and settings.yookassa_enabled():
        return "card"
    if preferred == "crypto" and settings.cryptobot_enabled():
        return "crypto"
    if settings.yookassa_enabled():
        return "card"
    if settings.cryptobot_enabled():
        return "crypto"
    return None


async def cryptobot_auto_worker(
    *,
    settings: Any,
    repo: Any,
    marzban: Any,
    bot: Any,
    stop_event: asyncio.Event,
    notify_admin_requeued_processing_fn: Any,
    cryptobot_check_invoice_fn: Any,
    apply_paid_payment_fn: Any,
    notify_access_updated_fn: Any,
) -> None:
    interval = max(10, settings.cryptobot_poll_seconds)
    while not stop_event.is_set():
        try:
            if settings.cryptobot_enabled():
                requeued = await repo.requeue_stuck_processing_payments(
                    "crypto",
                    older_than_sec=settings.payment_processing_requeue_seconds,
                    limit=100,
                )
                if requeued:
                    logging.warning(
                        "Auto crypto: requeued %s stuck processing payments",
                        len(requeued),
                    )
                    try:
                        await notify_admin_requeued_processing_fn(
                            bot=bot,
                            settings=settings,
                            provider="crypto",
                            rows=requeued,
                            older_than_sec=settings.payment_processing_requeue_seconds,
                        )
                    except Exception:
                        logging.exception("Auto crypto: requeue notify failed")
                payments = await repo.list_unfinished_crypto_payments(limit=100)
                for payment in payments:
                    external_id = str(payment["external_id"])
                    try:
                        status = await cryptobot_check_invoice_fn(settings, external_id)
                    except Exception:
                        logging.exception("Auto check failed for crypto payment %s", external_id)
                        continue

                    if status == "paid":
                        try:
                            updated, purpose, _ = await apply_paid_payment_fn(
                                provider="crypto",
                                external_id=external_id,
                                payment=payment,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                                bot=bot,
                                strict_device_slot=False,
                            )
                        except Exception:
                            logging.exception("Auto check: failed to apply payment %s", external_id)
                            await repo.set_payment_status("crypto", external_id, status)
                            continue
                        try:
                            if purpose == "device_add":
                                slot = int(payment.get("device_slot") or 0)
                                text = (
                                    f"✅ Оплата подтверждена. Устройство {slot} добавлено.\n"
                                    f"Назовите его командой: /device_name {slot} Мой ноутбук"
                                )
                            elif purpose == "plan_device":
                                slot = int(payment.get("device_slot") or 0)
                                text = f"✅ Оплата подтверждена автоматически. Устройство {slot} продлено."
                            elif purpose == "plan_all":
                                text = "✅ Оплата подтверждена автоматически. Все ключи продлены."
                            else:
                                text = "Оплата подтверждена автоматически. Доступ продлен."
                            await notify_access_updated_fn(
                                bot,
                                int(payment["telegram_id"]),
                                updated,
                                text,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                            )
                        except Exception:
                            logging.exception(
                                "Auto check: failed to notify user %s for payment %s",
                                payment["telegram_id"],
                                external_id,
                            )
                    else:
                        await repo.set_payment_status("crypto", external_id, status)
        except Exception:
            logging.exception("Auto crypto worker iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def yookassa_auto_worker(
    *,
    settings: Any,
    repo: Any,
    marzban: Any,
    bot: Any,
    stop_event: asyncio.Event,
    notify_admin_requeued_processing_fn: Any,
    yookassa_check_payment_fn: Any,
    apply_paid_payment_fn: Any,
    notify_access_updated_fn: Any,
) -> None:
    interval = max(20, settings.yookassa_poll_seconds)
    while not stop_event.is_set():
        try:
            if settings.yookassa_enabled():
                requeued = await repo.requeue_stuck_processing_payments(
                    "card",
                    older_than_sec=settings.payment_processing_requeue_seconds,
                    limit=100,
                )
                if requeued:
                    logging.warning(
                        "Auto yookassa: requeued %s stuck processing payments",
                        len(requeued),
                    )
                    try:
                        await notify_admin_requeued_processing_fn(
                            bot=bot,
                            settings=settings,
                            provider="card",
                            rows=requeued,
                            older_than_sec=settings.payment_processing_requeue_seconds,
                        )
                    except Exception:
                        logging.exception("Auto yookassa: requeue notify failed")
                payments = await repo.list_unfinished_payments("card", limit=100)
                for payment in payments:
                    external_id = str(payment.get("external_id") or "").strip()
                    if not external_id:
                        continue
                    status = await yookassa_check_payment_fn(settings, external_id)
                    paid = status == "succeeded"
                    if paid:
                        claimed = await repo.claim_payment_for_apply("card", external_id)
                        if not claimed:
                            continue
                        try:
                            updated, purpose, _ = await apply_paid_payment_fn(
                                provider="card",
                                external_id=external_id,
                                payment=payment,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                                bot=bot,
                                strict_device_slot=False,
                            )
                            if purpose == "device_add":
                                slot = int(payment.get("device_slot") or 0)
                                text = (
                                    f"✅ Оплата подтверждена. Устройство {slot} добавлено.\n"
                                    f"Назовите его командой: /device_name {slot} Мой ноутбук"
                                )
                            elif purpose == "plan_device":
                                slot = int(payment.get("device_slot") or 0)
                                text = f"✅ Оплата подтверждена автоматически. Устройство {slot} продлено."
                            elif purpose == "plan_all":
                                text = "✅ Оплата подтверждена автоматически. Все ключи продлены."
                            else:
                                text = "Оплата подтверждена автоматически. Доступ продлен."
                            await notify_access_updated_fn(
                                bot,
                                int(payment["telegram_id"]),
                                updated,
                                text,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                            )
                        except Exception:
                            logging.exception("Auto yookassa: failed to apply payment %s", external_id)
                            await repo.set_payment_status("card", external_id, status)
                    else:
                        await repo.set_payment_status("card", external_id, status)
        except Exception:
            logging.exception("Auto yookassa worker iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def subscription_renewal_worker(
    *,
    settings: Any,
    repo: Any,
    marzban: Any,
    bot: Any,
    stop_event: asyncio.Event,
    auto_renew_provider_fn: Any,
    auto_renew_plan_fn: Any,
    device_label_fn: Any,
    format_expire_fn: Any,
    format_time_left_fn: Any,
    renewal_actions_keyboard_fn: Any,
    plan_title_fn: Any,
    yookassa_create_payment_fn: Any,
    cryptobot_create_invoice_fn: Any,
    pay_action_keyboard_fn: Any,
) -> None:
    if (
        not settings.renewal_alerts_enabled
        and not settings.renewal_expired_alert_enabled
        and not settings.auto_renew_invoice_enabled
    ):
        return

    interval = max(60, settings.renewal_alert_interval_sec)
    reminder_hours = tuple(sorted({h for h in settings.renewal_reminder_hours if h > 0}))
    prune_every_sec = 6 * 3600
    last_prune = 0
    while not stop_event.is_set():
        try:
            now = int(time.time())
            if now - last_prune >= prune_every_sec:
                try:
                    await repo.prune_notification_marks(older_than_sec=180 * 86400)
                except Exception:
                    logging.exception("Renewal worker: prune notification marks failed")
                last_prune = now

            users = await repo.list_users()
            for user_row in users:
                tg_id = int(user_row["telegram_id"])
                devices = await repo.list_devices(tg_id)
                if not devices:
                    continue

                soonest_expire = 0
                soonest_slot = 1
                for row in devices:
                    device_id = int(row["device_id"])
                    username = str(row.get("marzban_username") or "").strip()
                    if not username:
                        continue
                    user = await marzban.get_user(username)
                    if not user:
                        continue

                    expire_ts = int(user.get("expire", 0) or 0)
                    if expire_ts <= 0:
                        continue
                    left = expire_ts - now
                    if soonest_expire == 0 or expire_ts < soonest_expire:
                        soonest_expire = expire_ts
                        soonest_slot = device_id

                    device_label = device_label_fn(device_id, row.get("device_name"))
                    if settings.renewal_expired_alert_enabled and left <= 0:
                        created = await repo.mark_notification_once(
                            telegram_id=tg_id,
                            device_id=device_id,
                            mark_type="renewal_expired",
                            expire_ts=expire_ts,
                        )
                        if created:
                            try:
                                await bot.send_message(
                                    tg_id,
                                    (
                                        f"⛔ Доступ для {device_label} истек.\n"
                                        f"Дата окончания: {format_expire_fn(expire_ts)}\n\n"
                                        "Нажмите продление ниже:"
                                    ),
                                    reply_markup=renewal_actions_keyboard_fn(device_id=device_id),
                                )
                            except Exception:
                                logging.exception(
                                    "Renewal worker: failed to send expired alert tg=%s slot=%s",
                                    tg_id,
                                    device_id,
                                )
                            try:
                                await repo.log_event(
                                    event_type="renewal_expired_notice",
                                    telegram_id=tg_id,
                                    event_meta={"slot": device_id, "expire": expire_ts},
                                )
                            except Exception:
                                logging.exception("Renewal worker: failed to track expired event")
                        continue

                    if not settings.renewal_alerts_enabled or left <= 0:
                        continue

                    threshold = next((h for h in reminder_hours if left <= h * 3600), None)
                    if threshold is None:
                        continue
                    created = await repo.mark_notification_once(
                        telegram_id=tg_id,
                        device_id=device_id,
                        mark_type=f"renewal_reminder_{threshold}h",
                        expire_ts=expire_ts,
                    )
                    if not created:
                        continue
                    try:
                        await bot.send_message(
                            tg_id,
                            (
                                f"⏰ Напоминание: срок доступа для {device_label} скоро закончится.\n"
                                f"Осталось: {format_time_left_fn(expire_ts)}\n"
                                f"До: {format_expire_fn(expire_ts)}\n\n"
                                "Нажмите продление ниже:"
                            ),
                            reply_markup=renewal_actions_keyboard_fn(device_id=device_id),
                        )
                    except Exception:
                        logging.exception(
                            "Renewal worker: failed to send reminder tg=%s slot=%s",
                            tg_id,
                            device_id,
                        )
                    try:
                        await repo.log_event(
                            event_type="renewal_reminder_notice",
                            telegram_id=tg_id,
                            event_meta={
                                "slot": device_id,
                                "expire": expire_ts,
                                "hours": threshold,
                            },
                        )
                    except Exception:
                        logging.exception("Renewal worker: failed to track reminder event")

                if not settings.auto_renew_invoice_enabled or soonest_expire <= 0:
                    continue
                left_for_auto = soonest_expire - now
                if left_for_auto <= 0 or left_for_auto > settings.auto_renew_invoice_hours_before * 3600:
                    continue
                provider = auto_renew_provider_fn(settings)
                if provider is None:
                    continue
                plan = auto_renew_plan_fn(settings)
                auto_target = settings.auto_renew_invoice_target
                if auto_target == "all":
                    purpose = "plan_all"
                    device_slot = 0
                    if await repo.has_open_plan_payment(
                        telegram_id=tg_id,
                        purpose=purpose,
                        device_slot=device_slot,
                    ):
                        continue
                    created = await repo.mark_notification_once(
                        telegram_id=tg_id,
                        device_id=0,
                        mark_type=f"auto_invoice_{provider}_{plan.key}_{auto_target}",
                        expire_ts=soonest_expire,
                    )
                    if not created:
                        continue
                    amount_rub = plan.rub * max(1, len(devices))
                    pay_desc = (
                        f"VPN {plan_title_fn(plan)}: автосчет на продление всех устройств "
                        f"({len(devices)} шт), +{plan.days}d"
                    )
                    pay_title = f"{plan_title_fn(plan)} / все устройства ({len(devices)} шт)"
                else:
                    purpose = "plan_device"
                    device_slot = soonest_slot
                    if await repo.has_open_plan_payment(
                        telegram_id=tg_id,
                        purpose=purpose,
                        device_slot=device_slot,
                    ):
                        continue
                    created = await repo.mark_notification_once(
                        telegram_id=tg_id,
                        device_id=device_slot,
                        mark_type=f"auto_invoice_{provider}_{plan.key}_{auto_target}",
                        expire_ts=soonest_expire,
                    )
                    if not created:
                        continue
                    amount_rub = plan.rub
                    pay_desc = f"VPN {plan_title_fn(plan)}: автосчет на продление устройства {device_slot}, +{plan.days}d"
                    pay_title = f"{plan_title_fn(plan)} / устройство {device_slot}"

                try:
                    if provider == "card":
                        external_id, pay_url = await yookassa_create_payment_fn(
                            settings,
                            tg_id,
                            amount_rub=amount_rub,
                            description=pay_desc,
                        )
                    else:
                        external_id, pay_url = await cryptobot_create_invoice_fn(
                            settings,
                            tg_id,
                            amount_rub=amount_rub,
                            description=pay_desc,
                        )
                    await repo.upsert_payment(
                        provider=provider,
                        external_id=external_id,
                        telegram_id=tg_id,
                        days=plan.days,
                        gb=plan.gb,
                        amount_rub=amount_rub,
                        pay_url=pay_url,
                        status="pending",
                        purpose=purpose,
                        device_slot=device_slot,
                    )
                    await repo.log_event(
                        event_type="payment_created_plan",
                        telegram_id=tg_id,
                        event_value=provider,
                        event_meta={
                            "external_id": external_id,
                            "amount_rub": amount_rub,
                            "purpose": purpose,
                            "device_slot": device_slot,
                            "plan_key": plan.key,
                            "auto_invoice": 1,
                        },
                    )
                    await bot.send_message(
                        tg_id,
                        (
                            "🔁 Автопродление: счет создан заранее.\n"
                            f"Тип: {pay_title}\n"
                            f"Сумма: {amount_rub:.2f} RUB\n"
                            f"Срок: +{plan.days} дней\n"
                            f"ID: {external_id}"
                        ),
                        reply_markup=pay_action_keyboard_fn(provider, external_id, pay_url),
                    )
                except Exception:
                    logging.exception("Renewal worker: auto invoice failed for tg=%s", tg_id)
        except Exception:
            logging.exception("Renewal worker iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
