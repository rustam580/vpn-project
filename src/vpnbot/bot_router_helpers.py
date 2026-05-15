from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.vpnbot.customer_profile import (
    ChatIdentity,
    CustomerProfileFormatters,
    build_customer_profile_text,
)
from src.vpnbot.web_order_profile import build_web_order_profile_lines


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


def _format_utc(ts: Any) -> str:
    value = int(ts or 0)
    if value <= 0:
        return "n/a"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


async def _send_telegram_user_lookup(*, message: Message, target_id: int, ctx: UserLookupContext) -> None:
    if target_id <= 0:
        await message.answer("ID must be a positive number.")
        return

    chat_identity: ChatIdentity | None = None
    try:
        chat = await message.bot.get_chat(target_id)
        chat_identity = ChatIdentity(
            first_name=str(chat.first_name or ""),
            last_name=str(chat.last_name or ""),
            username=str(chat.username or ""),
        )
    except Exception:
        pass

    text = await build_customer_profile_text(
        telegram_id=target_id,
        repo=ctx.repo,
        marzban=ctx.marzban,
        fmt=CustomerProfileFormatters(
            build_username=ctx.build_username,
            format_expire=ctx.format_expire,
            format_limit=ctx.format_limit,
            format_used=ctx.format_used,
            format_last_online=ctx.format_last_online,
            device_label=ctx.device_label,
        ),
        chat=chat_identity,
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Open chat", url=f"tg://user?id={target_id}")]
        ]
    )
    try:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as exc:
        if "BUTTON_USER_PRIVACY_RESTRICTED" not in str(exc):
            raise
        await message.answer(text + "\n\nOpen chat button is unavailable due to user privacy settings.", parse_mode="HTML")


async def _send_generic_user_lookup(*, message: Message, query: str, ctx: UserLookupContext) -> None:
    value = str(query or "").strip()
    if not value:
        await message.answer("Введите Telegram ID, order ID, email/контакт или Marzban username.")
        return

    lines: list[str] = [f"🔎 Поиск: <code>{html.escape(value)}</code>"]
    found_tg_ids: set[int] = set()
    found_any = False

    user_row = await ctx.repo.get_user_by_username(value)
    if user_row:
        found_any = True
        tg_id = int(user_row["telegram_id"])
        found_tg_ids.add(tg_id)
        lines.append(f"DB user: TG <code>{tg_id}</code> → <code>{html.escape(value)}</code>")

    device_row = await ctx.repo.get_device_by_username(value)
    if device_row:
        found_any = True
        tg_id = int(device_row["telegram_id"])
        found_tg_ids.add(tg_id)
        label = ctx.device_label(int(device_row["device_id"]), device_row.get("device_name"))
        lines.append(
            "DB device: "
            f"TG <code>{tg_id}</code>, слот <code>{int(device_row['device_id'])}</code>, "
            f"{html.escape(label)}"
        )

    payment = await ctx.repo.get_payment_any(value)
    if payment:
        found_any = True
        tg_id = int(payment["telegram_id"])
        found_tg_ids.add(tg_id)
        lines.append(
            "Платеж бота: "
            f"TG <code>{tg_id}</code>, {html.escape(str(payment.get('provider') or ''))}, "
            f"{html.escape(str(payment.get('purpose') or ''))}, "
            f"{float(payment.get('amount_rub') or 0):.2f} RUB, "
            f"{html.escape(str(payment.get('status') or ''))}, "
            f"{_format_utc(payment.get('updated_at'))}"
        )

    web_orders = await ctx.repo.find_web_orders(value, limit=5)
    if web_orders:
        found_any = True
        lines.append("Web orders:")
        for order in web_orders:
            order_lines, order_tg_ids = await build_web_order_profile_lines(
                order,
                repo=ctx.repo,
                marzban=ctx.marzban,
            )
            found_tg_ids.update(order_tg_ids)
            lines.extend(order_lines)

    is_possible_username = all(ch.isalnum() or ch in "._-" for ch in value)
    marzban_user = await ctx.marzban.get_user(value) if is_possible_username else None
    if marzban_user:
        found_any = True
        expire_ts = int(marzban_user.get("expire", 0) or 0)
        data_limit = int(marzban_user.get("data_limit", 0) or 0)
        used = int(marzban_user.get("used_traffic", 0) or 0)
        status = str(marzban_user.get("status", "unknown"))
        lines.append("Marzban direct:")
        lines.append(f"- Username: <code>{html.escape(value)}</code>")
        lines.append(f"- Статус: {html.escape(status)}")
        lines.append(f"- Действует до: {ctx.format_expire(expire_ts)}")
        lines.append(f"- Трафик: {ctx.format_used(used)} из {ctx.format_limit(data_limit)}")

    if not found_any:
        await message.answer(
            "Ничего не найдено. Можно искать по Telegram ID, order ID, внешнему ID платежа, "
            "email/контакту или Marzban username."
        )
        return

    if found_tg_ids:
        ids_text = ", ".join(f"<code>{tg_id}</code>" for tg_id in sorted(found_tg_ids))
        lines.append(f"Связанные Telegram ID: {ids_text}")
        if len(found_tg_ids) == 1:
            tg_id = next(iter(found_tg_ids))
            lines.append(f"Полная карточка: <code>/user {tg_id}</code>")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def send_user_lookup(
    *,
    message: Message,
    target_id: int | str,
    ctx: UserLookupContext,
) -> None:
    raw = str(target_id).strip()
    if raw.isdigit():
        await _send_telegram_user_lookup(message=message, target_id=int(raw), ctx=ctx)
        return
    await _send_generic_user_lookup(message=message, query=raw, ctx=ctx)


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
