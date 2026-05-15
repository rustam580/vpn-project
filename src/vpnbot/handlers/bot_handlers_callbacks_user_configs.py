from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, LinkPreviewOptions

from src.vpnbot.handlers.bot_handlers_callbacks_user_deps import UserCallbackDeps

NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


def register_config_callbacks(*, router: Router, deps: UserCallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_callback_rate_limit = deps.guard_callback_rate_limit
    collect_device_links = deps.collect_device_links
    send_configs_in_chat = deps.send_configs_in_chat
    render_config_block = deps.render_config_block

    @router.callback_query(F.data.startswith("cfg:"))
    async def cfg_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        items = await collect_device_links(
            telegram_id=int(callback.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        parts = callback.data.split(":")
        if len(parts) >= 2 and parts[1] == "showall":
            await callback.message.answer("Все активные ссылки:")
            await send_configs_in_chat(callback.message, items)
            await callback.answer()
            return
        if len(parts) == 3 and parts[1] == "show":
            try:
                index = int(parts[2])
            except ValueError:
                await callback.answer("Неверный формат", show_alert=True)
                return
            selected: tuple[str, str] | None = None
            counter = 1
            for _, label, link in items:
                if counter == index:
                    selected = (label, link)
                    break
                counter += 1
            if not selected:
                await callback.answer("Конфиг не найден", show_alert=True)
                return
            await callback.message.answer(
                render_config_block(selected[0], selected[1]),
                parse_mode="HTML",
                link_preview_options=NO_LINK_PREVIEW,
            )
            await callback.answer()
            return
        await callback.answer("Неверный callback", show_alert=True)
