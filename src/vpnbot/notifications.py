"""Outbound admin notification helpers.

These send Telegram messages to the configured admin chat(s):
- payment confirmations
- payments returned to pending from the processing state
- worker alerts (with per-key cooldown to avoid spam)

Module-level state (`_worker_alert_*`) is intentional: the cooldown applies
process-wide, not per-call.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from config import Settings
from src.vpnbot.bot_formatters import plan_gb_text


WORKER_ALERT_PREVIEW_LIMIT = 800
_worker_alert_last_sent: dict[str, float] = {}
_worker_alert_lock = asyncio.Lock()


async def notify_admin_payment(
    *,
    bot: Bot,
    settings: Settings,
    repo: Any,
    payment: dict[str, Any],
) -> None:
    try:
        tg_id = int(payment.get("telegram_id") or 0)
        if tg_id <= 0:
            return
        provider = str(payment.get("provider") or "")
        external_id = str(payment.get("external_id") or "")
        days = int(payment.get("days") or 0)
        gb = int(payment.get("gb") or 0)
        amount = float(payment.get("amount_rub") or 0)
        purpose = str(payment.get("purpose") or "plan")
        device_slot = payment.get("device_slot")

        chat = None
        try:
            chat = await bot.get_chat(tg_id)
        except Exception:
            chat = None
        name = ""
        username = ""
        if chat is not None:
            name_parts = [chat.first_name or "", chat.last_name or ""]
            name = " ".join(p for p in name_parts if p).strip()
            username = str(chat.username or "").strip()

        marzban_username = ""
        row = await repo.get_user(tg_id)
        if row:
            marzban_username = str(row.get("marzban_username") or "")

        lines = [
            "💳 Оплата подтверждена",
            f"Провайдер: {html.escape(provider)}",
            f"Сумма: {amount:.2f} RUB",
        ]
        if purpose == "device_add":
            slot_text = f", слот {device_slot}" if device_slot else ""
            lines.append(f"Тип: дополнительное устройство{slot_text}")
        else:
            lines.append(
                f"Тариф: {days} дн., {plan_gb_text(gb)}"
            )
        if external_id:
            lines.append(f"Payment ID: {html.escape(external_id)}")
        link = f'<a href="tg://user?id={tg_id}">ID {tg_id}</a>'
        user_line = f"Пользователь: {link}"
        if name:
            user_line += f" ({html.escape(name)})"
        if username:
            user_line += f" @{html.escape(username)}"
        lines.append(user_line)
        if marzban_username:
            lines.append(f"Marzban: {html.escape(marzban_username)}")

        text = "\n".join(lines)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(int(admin_id), text, parse_mode="HTML")
            except Exception:
                logging.exception("Payment notify: failed to send to admin %s", admin_id)
    except Exception:
        logging.exception("Payment notify: failed to build admin message")


async def notify_admin_requeued_processing(
    *,
    bot: Bot,
    settings: Settings,
    provider: str,
    rows: list[dict[str, Any]],
    older_than_sec: int,
) -> None:
    if not rows:
        return
    lines = [
        "⚠️ Платежи возвращены из processing в pending",
        f"Провайдер: {provider}",
        f"Порог: {older_than_sec} сек",
        f"Количество: {len(rows)}",
    ]
    preview = rows[:8]
    for row in preview:
        external_id = str(row.get("external_id") or "")
        tg_id = int(row.get("telegram_id") or 0)
        lines.append(f"- {external_id} (tg:{tg_id})")
    if len(rows) > len(preview):
        lines.append(f"...и еще {len(rows) - len(preview)}")
    text = "\n".join(lines)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(int(admin_id), text)
        except Exception:
            logging.exception("Requeue notify: failed to send to admin %s", admin_id)


async def notify_admin_worker_alert(
    *,
    bot: Bot,
    settings: Settings,
    key: str,
    title: str,
    details: str = "",
) -> None:
    if not settings.admin_alerts_enabled:
        return

    now = time.time()
    cooldown = max(0, settings.admin_alert_cooldown_sec)
    if cooldown > 0:
        async with _worker_alert_lock:
            last_sent = _worker_alert_last_sent.get(key, 0.0)
            if now - last_sent < cooldown:
                return
            _worker_alert_last_sent[key] = now

    details_text = details.strip()
    if len(details_text) > WORKER_ALERT_PREVIEW_LIMIT:
        details_text = details_text[:WORKER_ALERT_PREVIEW_LIMIT].rstrip() + "..."

    lines = [
        f"🚨 {title}",
        f"Ключ: {key}",
        f"Время: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M:%S')} UTC",
    ]
    if details_text:
        lines.append(f"Детали: {details_text}")
    text = "\n".join(lines)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(int(admin_id), text)
        except Exception:
            logging.exception("Worker alert: failed to send to admin %s", admin_id)
