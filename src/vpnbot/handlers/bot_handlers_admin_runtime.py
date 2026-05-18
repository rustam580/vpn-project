"""Admin-facing command and button handlers extracted from build_router."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import LinkPreviewOptions, Message

from src.vpnbot.keyboards.bot_keyboards import admin_panel_keyboard
from src.vpnbot.message_utils import split_message
from src.vpnbot.olcrtc_rescue import (
    build_deploy_steps,
    build_rescue_admin_summary,
    build_rescue_user_message,
    create_local_session,
    parse_rescue_command_args,
    run_steps_async,
)
from src.vpnbot.permissions import is_admin

NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


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
    track_event: Callable[..., Awaitable[None]] | None = None


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
    track_event = deps.track_event

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

    @router.message(Command("rescue"))
    async def rescue_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        try:
            args = parse_rescue_command_args(message.text or "")
            session = create_local_session(room=args.room, tg_id=str(args.target_tg_id))
        except ValueError:
            await message.answer(
                "Использование: /rescue <telegram_id> <wb_room_url>\n\n"
                "Пример:\n"
                "/rescue 386029735 https://stream.wb.ru/room/019e..."
            )
            return
        except Exception as exc:
            await message.answer(f"Не удалось создать Rescue-сессию: {exc}")
            return

        if track_event is not None:
            await track_event(
                "olcrtc_rescue_session_created",
                telegram_id=args.target_tg_id,
                event_value=session.session_id,
                event_meta={
                    "room_id": session.room_id,
                    "client_id": session.client_id,
                    "out_dir": str(session.out_dir),
                },
            )

        auto_deploy_enabled = bool(getattr(settings, "olcrtc_rescue_auto_deploy", False))
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        deploy_text = "auto_deploy: disabled"
        can_deliver_user_message = not auto_deploy_enabled
        if auto_deploy_enabled:
            if not deploy_host:
                deploy_text = "auto_deploy: enabled, but OLCRTC_RESCUE_DEPLOY_HOST is empty"
                can_deliver_user_message = False
            else:
                await message.answer(f"Rescue-сессия создана: {session.session_id}. Запускаю deploy на {deploy_host}...")
                steps = build_deploy_steps(
                    session_id=session.session_id,
                    local_dir=session.out_dir,
                    deploy_host=deploy_host,
                    remote_root=str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue")),
                    install_service=bool(getattr(settings, "olcrtc_rescue_install_service", True)),
                    start_service=True,
                    safe_ssh=True,
                )
                result = await run_steps_async(
                    steps,
                    timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
                )
                deploy_text = (
                    f"auto_deploy: ok\n{result.output}"
                    if result.ok
                    else f"auto_deploy: failed at {result.failed_step}\n{result.output}"
                )
                can_deliver_user_message = result.ok

        user_message = build_rescue_user_message(session.uri)
        delivered = False
        if can_deliver_user_message:
            try:
                await message.bot.send_message(
                    args.target_tg_id,
                    user_message,
                    link_preview_options=NO_LINK_PREVIEW,
                )
                delivered = True
            except Exception:
                delivered = False

        admin_text = build_rescue_admin_summary(session, deploy_host=deploy_host or "root@104.238.29.239")
        admin_text += f"\n\n{deploy_text}"
        admin_text += "\n\nuser_delivery: " + ("sent" if delivered else "not_sent")
        for chunk in split_message(admin_text, limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)

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
