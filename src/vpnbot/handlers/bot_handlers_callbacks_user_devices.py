from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.vpnbot.handlers.bot_handlers_callbacks_user_deps import UserCallbackDeps


def register_device_callbacks(*, router: Router, deps: UserCallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_callback_rate_limit = deps.guard_callback_rate_limit
    pending_device_rename = deps.pending_device_rename
    replace_device_slot = deps.replace_device_slot
    send_status = deps.send_status
    send_device_links = deps.send_device_links
    device_replace_confirm_keyboard = deps.device_replace_confirm_keyboard
    device_label = deps.device_label

    @router.callback_query(F.data.startswith("devrename:"))
    async def device_rename_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        _, value = callback.data.split(":", 1)
        if value == "cancel":
            pending_device_rename.pop(int(callback.from_user.id), None)
            await callback.answer("Отменено")
            return
        try:
            device_id = int(value)
        except ValueError:
            await callback.answer("Неверный формат", show_alert=True)
            return
        if device_id < 1:
            await callback.answer("Неверный ID", show_alert=True)
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await callback.answer("ID вне лимита", show_alert=True)
            return
        row = await repo.get_device(int(callback.from_user.id), device_id)
        if not row:
            await callback.answer("Устройство не найдено", show_alert=True)
            return
        pending_device_rename[int(callback.from_user.id)] = device_id
        await callback.message.answer(
            f"Введите новое имя для устройства {device_id} (пример: Мой ноутбук)."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("devreplace:"))
    async def device_replace_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        _, value = callback.data.split(":", 1)
        if value == "cancel":
            await callback.answer("Отменено")
            return
        try:
            device_id = int(value)
        except ValueError:
            await callback.answer("Неверный формат", show_alert=True)
            return
        if device_id < 1:
            await callback.answer("Неверный ID", show_alert=True)
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await callback.answer("ID вне лимита", show_alert=True)
            return
        row = await repo.get_device(int(callback.from_user.id), device_id)
        if not row:
            await callback.answer("Устройство не найдено", show_alert=True)
            return
        username = str(row.get("marzban_username") or "").strip()
        user = await marzban.get_user(username) if username else None
        if not user or str(user.get("status", "unknown")) != "active":
            await callback.answer("Устройство не активно", show_alert=True)
            return
        label = device_label(device_id, row.get("device_name"))
        await callback.message.answer(
            f"Подтвердите перевыпуск ссылки для устройства {device_id} ({label}).\n"
            "Старая ссылка этого устройства будет отключена.",
            reply_markup=device_replace_confirm_keyboard(device_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("devreplace_confirm:"))
    async def device_replace_confirm_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Неверный callback", show_alert=True)
            return
        _, raw_device_id, decision = parts
        try:
            device_id = int(raw_device_id)
        except ValueError:
            await callback.answer("Неверный ID", show_alert=True)
            return
        if decision != "yes":
            await callback.answer("Отменено")
            return
        if device_id < 1 or (settings.device_limit > 0 and device_id > settings.device_limit):
            await callback.answer("ID вне лимита", show_alert=True)
            return
        tg_id = int(callback.from_user.id)
        try:
            _, _, new_user = await replace_device_slot(
                telegram_id=tg_id,
                slot=device_id,
            )
        except Exception as exc:
            logging.exception("User device_replace failed for tg=%s slot=%s", tg_id, device_id)
            await callback.answer("Не удалось перевыпустить ссылку", show_alert=True)
            await callback.message.answer(f"Ошибка перевыпуска ссылки: {exc}")
            return
        await callback.answer("Готово")
        await callback.message.answer(
            f"🔁 Ссылка устройства {device_id} перевыпущена.\n"
            "Старая ссылка этого устройства отключена.\n"
            "Импортируйте новую ссылку из списка ниже.\n"
            "Важно: одна ссылка = одно устройство."
        )
        if device_id == 1:
            await send_status(callback.message, new_user)
        await send_device_links(
            message=callback.message,
            telegram_id=tg_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
