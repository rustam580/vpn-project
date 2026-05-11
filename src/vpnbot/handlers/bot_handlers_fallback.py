from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message


@dataclass
class FallbackDeps:
    settings: Any
    repo: Any
    marzban: Any
    guard_message_rate_limit: Any
    pending_user_lookup: set[int]
    pending_device_add_prompt: set[int]
    pending_broadcast_prompt: set[int]
    pending_broadcast_text: dict[int, str]
    pending_broadcast_format: dict[int, str]
    pending_broadcast_buttons: dict[int, bool]
    pending_device_rename: dict[int, int]
    pending_issue: set[int]
    send_user_lookup: Any
    ensure_device: Any
    send_broadcast_preview: Any
    normalize_device_name: Any
    track_event: Any
    keyboard_for_user: Any
    is_admin_fn: Any


def register_fallback_handler(*, router: Router, deps: FallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_message_rate_limit = deps.guard_message_rate_limit
    pending_user_lookup = deps.pending_user_lookup
    pending_device_add_prompt = deps.pending_device_add_prompt
    pending_broadcast_prompt = deps.pending_broadcast_prompt
    pending_broadcast_text = deps.pending_broadcast_text
    pending_broadcast_format = deps.pending_broadcast_format
    pending_broadcast_buttons = deps.pending_broadcast_buttons
    pending_device_rename = deps.pending_device_rename
    pending_issue = deps.pending_issue
    send_user_lookup = deps.send_user_lookup
    ensure_device = deps.ensure_device
    send_broadcast_preview = deps.send_broadcast_preview
    normalize_device_name = deps.normalize_device_name
    track_event = deps.track_event
    keyboard_for_user = deps.keyboard_for_user
    is_admin_fn = deps.is_admin_fn

    @router.message()
    async def fallback_menu(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        if tg_id in pending_user_lookup:
            if not message.text:
                await message.answer("Введите Telegram ID, order ID, email/контакт или Marzban username. Или «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_user_lookup.discard(tg_id)
                await message.answer("Ок, отменено.")
                return
            if text.startswith("/"):
                await message.answer("Введите Telegram ID, order ID, email/контакт или Marzban username. Или «отмена».")
                return
            pending_user_lookup.discard(tg_id)
            await send_user_lookup(message, text)
            return
        if tg_id in pending_device_add_prompt:
            if not message.text:
                await message.answer("Введите Telegram ID и слот (опционально) или «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_device_add_prompt.discard(tg_id)
                await message.answer("Ок, отменено.")
                return
            if text.startswith("/"):
                await message.answer("Введите Telegram ID и слот (опционально) или «отмена».")
                return
            parts = text.split()
            if len(parts) not in {1, 2}:
                await message.answer("Формат: <telegram_id> [slot]. Пример: 386029735 2")
                return
            try:
                target = int(parts[0])
                slot = int(parts[1]) if len(parts) == 2 else 2
            except ValueError:
                await message.answer("ID и слот должны быть числами. Пример: 386029735 2")
                return
            if slot < 1:
                await message.answer("Слот должен быть >= 1")
                return
            if settings.device_limit > 0 and slot > settings.device_limit:
                await message.answer(f"Слот должен быть 1..{settings.device_limit}")
                return
            pending_device_add_prompt.discard(tg_id)
            _, user, created = await ensure_device(
                telegram_id=target,
                device_id=slot,
                repo=repo,
                marzban=marzban,
                settings=settings,
                create_if_missing=True,
            )
            if not user:
                await message.answer("Не удалось создать устройство.")
                return
            msg = f"Устройство {slot} создано." if created else f"Устройство {slot} уже существует."
            await message.answer(msg)
            return
        if tg_id in pending_broadcast_prompt:
            if not message.text:
                await message.answer("Введите текст рассылки или «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_broadcast_prompt.discard(tg_id)
                pending_broadcast_text.pop(tg_id, None)
                pending_broadcast_format.pop(tg_id, None)
                pending_broadcast_buttons.pop(tg_id, None)
                await message.answer("Рассылка отменена.")
                return
            if text.startswith("/"):
                await message.answer("Введите текст рассылки или напишите «отмена».")
                return
            pending_broadcast_prompt.discard(tg_id)
            pending_broadcast_text[tg_id] = text
            pending_broadcast_format.setdefault(tg_id, "plain")
            pending_broadcast_buttons.setdefault(tg_id, True)
            await send_broadcast_preview(message, text)
            return
        if tg_id in pending_device_rename:
            if not message.text:
                await message.answer("Введите текстовое имя устройства.")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_device_rename.pop(tg_id, None)
                await message.answer("Переименование отменено.")
                return
            if text.startswith("/"):
                await message.answer("Введите текстовое имя устройства или напишите «отмена».")
                return
            name = normalize_device_name(text)
            if not name:
                await message.answer("Имя устройства не может быть пустым.")
                return
            device_id = pending_device_rename.pop(tg_id)
            await repo.set_device_name(tg_id, device_id, name)
            await message.answer(f"✅ Устройство {device_id} теперь называется: {name}")
            return
        if tg_id in pending_issue:
            if not message.text:
                await message.answer("Отправьте текстовое описание проблемы или напишите «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_issue.discard(tg_id)
                await message.answer("Ок, отменено.")
                return
            if text.startswith("/"):
                await message.answer("Отправьте описание проблемы или напишите «отмена».")
                return
            pending_issue.discard(tg_id)
            username = message.from_user.username or ""
            header = f"🚨 Проблема с подключением\nTG: {tg_id}"
            if username:
                header += f" (@{username})"
            report = f"{header}\n\n{text}"
            await track_event(
                "issue_reported",
                telegram_id=tg_id,
                event_meta={"text_len": len(text)},
            )
            for admin_id in settings.admin_ids:
                try:
                    await message.bot.send_message(int(admin_id), report)
                except Exception:
                    logging.exception("Failed to notify admin %s about issue", admin_id)
            await message.answer("Спасибо, отправили админу. Если нужно, мы уточним детали.")
            return
        if message.text and message.text.startswith("/"):
            # Let dedicated command handlers process slash-commands.
            raise SkipHandler()
        await message.answer(
            "Открыл меню.",
            reply_markup=keyboard_for_user(is_admin=is_admin_fn(tg_id, settings)),
        )
