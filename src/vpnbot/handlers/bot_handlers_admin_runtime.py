"""Admin-facing command and button handlers extracted from build_router."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.vpnbot.keyboards.bot_keyboards import admin_panel_keyboard
from src.vpnbot.permissions import is_admin


@dataclass
class AdminRuntimeDeps:
    """Dependencies for admin-facing runtime handlers."""

    settings: Any
    guard_message_rate_limit: Callable[[Message], Awaitable[bool]]
    handle_grant_perm: Callable[[Message], Awaitable[bool]]
    send_broadcast_preview: Callable[..., Awaitable[None]]
    send_user_lookup: Callable[[Message, int | str], Awaitable[None]]
    pending_broadcast_prompt: set[int]
    pending_broadcast_format: dict[int, str]
    pending_broadcast_buttons: dict[int, bool]
    pending_broadcast_text: dict[int, str]


def register_admin_runtime_handlers(*, router: Router, deps: AdminRuntimeDeps) -> None:
    settings = deps.settings
    guard_message_rate_limit = deps.guard_message_rate_limit
    handle_grant_perm = deps.handle_grant_perm
    send_broadcast_preview = deps.send_broadcast_preview
    send_user_lookup = deps.send_user_lookup
    pending_broadcast_prompt = deps.pending_broadcast_prompt
    pending_broadcast_format = deps.pending_broadcast_format
    pending_broadcast_buttons = deps.pending_broadcast_buttons
    pending_broadcast_text = deps.pending_broadcast_text

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
            await message.answer("Использование: /user <telegram_id|order_id|email|marzban_username>")
            return
        target_id = parts[1].strip()
        await send_user_lookup(message, target_id)

    @router.message(F.text == "🛠 Админ-кабинет")
    async def admin_btn(message: Message) -> None:
        await admin_cmd(message)
