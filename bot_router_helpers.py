from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


@dataclass
class UserLookupContext:
    repo: Any
    marzban: Any
    build_username: Any
    format_expire: Any
    format_limit: Any
    format_used: Any
    format_last_online: Any
    device_label: Any


@dataclass
class BroadcastPreviewContext:
    repo: Any
    pending_broadcast_format: dict[int, str]
    pending_broadcast_buttons: dict[int, bool]
    broadcast_format_label: Any
    broadcast_parse_mode: Any
    broadcast_confirm_keyboard: Any


async def send_user_lookup(
    *,
    message: Message,
    target_id: int,
    ctx: UserLookupContext,
) -> None:
    if target_id <= 0:
        await message.answer("ID должен быть положительным числом.")
        return
    chat = None
    try:
        chat = await message.bot.get_chat(target_id)
    except Exception:
        pass

    lines: list[str] = []
    link = f'<a href="tg://user?id={target_id}">ID {target_id}</a>'
    lines.append(f"👤 Пользователь: {link}")
    if chat is not None:
        name_parts = [chat.first_name or "", chat.last_name or ""]
        name = " ".join(p for p in name_parts if p).strip()
        if name:
            lines.append(f"Имя: {html.escape(name)}")
        username = str(chat.username or "").strip()
        if username:
            lines.append(f"Username: @{html.escape(username)}")

    row = await ctx.repo.get_user(target_id)
    marzban_user = None
    if row:
        username = str(row["marzban_username"])
        marzban_user = await ctx.marzban.get_user(username)
        if marzban_user:
            lines.append(f"Marzban: {html.escape(username)}")
        else:
            lines.append(f"Marzban: {html.escape(username)} (не найден)")
    else:
        guessed = ctx.build_username(target_id)
        marzban_user = await ctx.marzban.get_user(guessed)
        if marzban_user:
            lines.append(f"Marzban: {html.escape(guessed)}")
        else:
            lines.append("Marzban: не найден")

    if marzban_user:
        expire_ts = int(marzban_user.get("expire", 0) or 0)
        data_limit = int(marzban_user.get("data_limit", 0) or 0)
        used = int(marzban_user.get("used_traffic", 0) or 0)
        status = str(marzban_user.get("status", "unknown"))
        lines.append(f"Статус: {html.escape(status)}")
        lines.append(f"Действует до: {ctx.format_expire(expire_ts)}")
        lines.append(f"Трафик: {ctx.format_used(used)} из {ctx.format_limit(data_limit)}")

    devices = await ctx.repo.list_devices(target_id)
    if devices:
        lines.append("Устройства:")
        for row in devices:
            device_id = int(row["device_id"])
            label = ctx.device_label(device_id, row.get("device_name"))
            username = str(row.get("marzban_username") or "")
            mz_user = await ctx.marzban.get_user(username) if username else None
            if not mz_user:
                state = "не найден в Marzban"
            else:
                state = (
                    f"{mz_user.get('status', 'unknown')}, "
                    f"online: {ctx.format_last_online(mz_user.get('online_at') or mz_user.get('last_online') or mz_user.get('last_online_at'))}, "
                    f"traffic: {ctx.format_used(int(mz_user.get('used_traffic', 0) or 0))}"
                )
            if label.startswith("Устройство"):
                lines.append(
                    f"- {device_id}. {html.escape(label)} ({html.escape(username)}): {html.escape(state)}"
                )
            else:
                lines.append(
                    f"- {device_id}. Устройство {device_id} — {html.escape(label)} ({html.escape(username)}): {html.escape(state)}"
                )
    else:
        lines.append("Устройства: нет")

    latest_payment = await ctx.repo.get_latest_payment(target_id)
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
            f"Последний платеж: {html.escape(provider)}, {html.escape(purpose)}, "
            f"{amount:.2f} RUB, {html.escape(status)}, {updated_text}"
        )
    else:
        lines.append("Последний платеж: нет данных")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть диалог", url=f"tg://user?id={target_id}")]
        ]
    )
    text = "\n".join(lines)
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as exc:
        if "BUTTON_USER_PRIVACY_RESTRICTED" not in str(exc):
            raise
        await message.answer(
            text + "\n\n⚠️ Кнопка «Открыть диалог» недоступна из-за privacy-настроек пользователя.",
            parse_mode="HTML",
        )


async def send_broadcast_preview(
    *,
    message: Message,
    body: str,
    ctx: BroadcastPreviewContext,
    admin_id: int | None = None,
) -> None:
    if admin_id is None:
        if not message.from_user:
            return
        admin_id = int(message.from_user.id)
    targets = {int(tg_id) for tg_id in await ctx.repo.list_known_telegram_ids()}
    count_total = len(targets)
    count_without_admin = len({tg for tg in targets if tg != admin_id})
    fmt_key = ctx.pending_broadcast_format.get(admin_id, "plain")
    with_buttons = ctx.pending_broadcast_buttons.get(admin_id, True)
    fmt_label = ctx.broadcast_format_label(fmt_key)
    buttons_label = "вкл" if with_buttons else "выкл"
    preview = (
        f"📣 Рассылка (получателей: {count_without_admin}, всего: {count_total})\n"
        f"Формат: {fmt_label}\n"
        f"Кнопки: {buttons_label}\n\n"
        f"{body}"
    )
    parse_mode = ctx.broadcast_parse_mode(fmt_key)
    kwargs: dict[str, Any] = {}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    try:
        await message.answer(
            preview,
            reply_markup=ctx.broadcast_confirm_keyboard(fmt_key=fmt_key, with_buttons=with_buttons),
            **kwargs,
        )
    except Exception:
        logging.exception("Broadcast preview failed")
        await message.answer(
            "Не удалось показать предпросмотр с форматированием. Проверьте разметку или выберите «Текст».",
            reply_markup=ctx.broadcast_confirm_keyboard(fmt_key=fmt_key, with_buttons=with_buttons),
        )
