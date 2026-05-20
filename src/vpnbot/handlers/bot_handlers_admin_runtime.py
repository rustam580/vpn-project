"""Admin-facing command and button handlers extracted from build_router."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from src.vpnbot.keyboards.bot_keyboards import admin_panel_keyboard
from src.vpnbot.message_utils import split_message
from src.vpnbot.olcrtc_rescue import (
    active_rescue_sessions_for_room,
    active_rescue_session_ids,
    build_deploy_steps,
    build_rescue_admin_summary,
    build_rescue_uri_for_room,
    build_rescue_user_message,
    create_local_session,
    diagnose_rescue_status_output,
    default_client_id,
    fetch_rescue_list,
    fetch_rescue_status,
    format_rescue_dashboard,
    normalize_rescue_room_url,
    parse_rescue_list_output,
    parse_rescue_command_args,
    run_steps_async,
    stop_rescue_session,
    validate_session_id,
    wait_for_rescue_session_active,
)
from src.vpnbot.permissions import is_admin

NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


def rescue_status_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Статус Rescue", callback_data=f"admin:rescue_status:{session_id}")]
        ]
    )


@dataclass
class AdminRuntimeDeps:
    """Dependencies for admin-facing runtime handlers."""

    settings: Any
    repo: Any
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
    repo = deps.repo
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
                existing = await fetch_rescue_list(
                    deploy_host=deploy_host,
                    remote_root=str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue")),
                    timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
                )
                if existing.ok:
                    duplicate_sessions = active_rescue_sessions_for_room(session.room_url, existing.output)
                    if duplicate_sessions:
                        duplicate = duplicate_sessions[0]
                        await message.answer(
                            "Rescue-сессия для этой WB-комнаты уже активна.\n\n"
                            f"session_id: {duplicate.session_id}\n"
                            f"room: {duplicate.room_url}\n"
                            f"since: {duplicate.since or '-'}\n\n"
                            "Я не запускаю второй relay в той же комнате, чтобы они не мешали друг другу.\n"
                            f"Проверьте: /rescue_status {duplicate.session_id}\n"
                            f"Если нужно пересоздать: /rescue_stop {duplicate.session_id}, затем повторите /rescue."
                        )
                        return
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
        await message.answer(
            f"Статус сессии: {session.session_id}",
            reply_markup=rescue_status_keyboard(session.session_id),
        )


    @router.message(Command("rescue_room_add"))
    async def rescue_room_add_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ.")
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await message.answer("Usage: /rescue_room_add <wb_room_url> [note]")
            return
        room_url = normalize_rescue_room_url(parts[1].strip())
        room_id = room_url.rsplit("/", 1)[-1]
        note = parts[2].strip() if len(parts) == 3 else ""
        row = await repo.add_rescue_room(room_id=room_id, room_url=room_url, note=note)
        await message.answer(
            "Rescue room saved.\n"
            f"id: {row['id']}\n"
            f"status: {row['status']}\n"
            f"room: {row['room_url']}\n"
            f"note: {row['note'] or '-'}"
        )

    @router.message(Command("rescue_room_warm"))
    async def rescue_room_warm_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Usage: /rescue_room_warm <room_id_or_wb_room_url>")
            return
        room_url = normalize_rescue_room_url(parts[1].strip())
        room_id = room_url.rsplit("/", 1)[-1]
        room = await repo.get_rescue_room_by_room_id(room_id)
        if room is None:
            await message.answer("Room is not in pool. Add it first: /rescue_room_add <wb_room_url> [note]")
            return
        if str(room["status"]) in {"assigned", "reserved"}:
            await message.answer(f"Room is {room['status']}; not warming it.")
            return
        if str(room["status"]) == "warm" and room["session_id"]:
            await message.answer(
                "Room is already warm.\n"
                f"session: {room['session_id']}\n"
                f"room: {room['room_url']}"
            )
            return
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not bool(getattr(settings, "olcrtc_rescue_auto_deploy", False)) or not deploy_host:
            await message.answer("Warm mode requires OLCRTC_RESCUE_AUTO_DEPLOY=1 and OLCRTC_RESCUE_DEPLOY_HOST.")
            return

        session = create_local_session(room=room_url, client_id="olcbox")
        await message.answer(
            "Warming Rescue room.\n"
            f"room: {session.room_url}\n"
            f"session: {session.session_id}\n"
            f"Deploying on {deploy_host}..."
        )
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
        if not result.ok:
            await repo.mark_rescue_room_status(
                room_id=room_id,
                status="free",
                increment_fail_count=True,
            )
            for chunk in split_message(f"Warm deploy failed at {result.failed_step}\n{result.output}", limit=3500):
                await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)
            return

        await repo.mark_rescue_room_warm(
            room_id=room_id,
            session_id=session.session_id,
            key_hex=session.key_hex,
            client_id=session.client_id,
            uri=session.uri,
        )
        await message.answer(
            "Rescue room is warm.\n"
            f"session: {session.session_id}\n"
            f"room: {session.room_url}\n"
            "Now you can leave WB as host; server relay should keep the room alive."
        )

    @router.message(Command("rescue_rooms"))
    async def rescue_rooms_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ.")
            return
        rows = await repo.list_rescue_rooms()
        if not rows:
            await message.answer("Rescue room pool is empty.\nAdd room: /rescue_room_add <wb_room_url> [note]")
            return
        lines = ["🏊 Rescue Room Pool", f"rooms: {len(rows)}", ""]
        for idx, row in enumerate(rows, start=1):
            lines.extend(
                [
                    f"{idx}. {row['status']} | {row['room_id']}",
                    f"   tg: {row['assigned_tg_id'] or '-'}",
                    f"   session: {row['session_id'] or '-'}",
                    f"   key: {'yes' if row.get('key_hex') else 'no'}",
                    f"   fails: {row['fail_count'] or 0}",
                    f"   room: {row['room_url']}",
                    f"   note: {row['note'] or '-'}",
                    "",
                ]
            )
        for chunk in split_message("\n".join(lines).rstrip(), limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)

    @router.message(Command("rescue_reconcile"))
    async def rescue_reconcile_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        auto_deploy_enabled = bool(getattr(settings, "olcrtc_rescue_auto_deploy", False))
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not auto_deploy_enabled or not deploy_host:
            await message.answer("Rescue reconcile requires OLCRTC_RESCUE_AUTO_DEPLOY=1 and OLCRTC_RESCUE_DEPLOY_HOST.")
            return

        apply_changes = (message.text or "").split(maxsplit=1)[1:] == ["apply"]
        result = await fetch_rescue_list(
            deploy_host=deploy_host,
            remote_root=str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue")),
            timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
        )
        if not result.ok:
            await message.answer(f"Could not list Rescue sessions on {deploy_host}.\n{result.output}")
            return

        remote_sessions = parse_rescue_list_output(result.output)
        active_ids = active_rescue_session_ids(remote_sessions)
        remote_by_id = {session.session_id: session for session in remote_sessions}
        rows = await repo.list_rescue_rooms()
        stale_rows = [
            row
            for row in rows
            if str(row.get("status") or "") in {"warm", "assigned"}
            and str(row.get("session_id") or "").strip()
            and str(row.get("session_id") or "").strip() not in active_ids
        ]

        changed = 0
        if apply_changes:
            for row in stale_rows:
                session_id = str(row.get("session_id") or "").strip()
                telegram_id = row.get("assigned_tg_id")
                await repo.mark_rescue_room_status(
                    room_id=str(row["room_id"]),
                    status="bad",
                    session_id=session_id,
                    telegram_id=int(telegram_id) if telegram_id else None,
                    increment_fail_count=True,
                )
                changed += 1

        lines = [
            "Rescue reconcile: " + ("applied" if apply_changes else "dry-run"),
            f"host: {deploy_host}",
            f"remote sessions: {len(remote_sessions)} total / {len(active_ids)} active",
            f"stale warm/assigned rows: {len(stale_rows)}",
            "",
        ]
        if stale_rows:
            for idx, row in enumerate(stale_rows, start=1):
                session_id = str(row.get("session_id") or "").strip()
                remote_status = remote_by_id.get(session_id).active if session_id in remote_by_id else "missing"
                lines.extend(
                    [
                        f"{idx}. {row['status']} -> bad | {row['room_id']}",
                        f"   tg: {row['assigned_tg_id'] or '-'}",
                        f"   session: {session_id}",
                        f"   remote: {remote_status}",
                        f"   room: {row['room_url']}",
                        "",
                    ]
                )
            if not apply_changes:
                lines.append("Apply cleanup: /rescue_reconcile apply")
        elif not apply_changes:
            lines.append("Nothing to clean.")
        if apply_changes:
            lines.append(f"changed: {changed}")

        for chunk in split_message("\n".join(lines).rstrip(), limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)

    @router.message(Command("rescue_create"))
    async def rescue_create_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Usage: /rescue_create <telegram_id>")
            return
        try:
            target_tg_id = int(parts[1].strip())
            if target_tg_id <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Invalid telegram_id.")
            return
        auto_deploy_enabled = bool(getattr(settings, "olcrtc_rescue_auto_deploy", False))
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not auto_deploy_enabled or not deploy_host:
            await message.answer("Rescue pool requires OLCRTC_RESCUE_AUTO_DEPLOY=1 and OLCRTC_RESCUE_DEPLOY_HOST.")
            return

        active_warm_session_ids: set[str] = set()
        existing = await fetch_rescue_list(
            deploy_host=deploy_host,
            remote_root=str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue")),
            timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
        )
        if existing.ok:
            active_warm_session_ids = active_rescue_session_ids(parse_rescue_list_output(existing.output))

        room = await repo.claim_next_free_rescue_room(
            telegram_id=target_tg_id,
            active_warm_session_ids=active_warm_session_ids,
        )
        if room is None:
            await message.answer(
                "No usable Rescue rooms in pool.\n"
                "Warm rooms are only usable when their relay is active on the Rescue VPS.\n"
                "Add one: /rescue_room_add <wb_room_url> [note]"
            )
            return

        if str(room.get("claimed_from_status") or "") == "warm":
            key_hex = str(room.get("key_hex") or "").strip()
            session_id = str(room.get("session_id") or "").strip()
            if not key_hex or not session_id:
                await repo.mark_rescue_room_status(
                    room_id=str(room["room_id"]),
                    status="free",
                    increment_fail_count=True,
                )
                await message.answer("Warm room has no key/session metadata; returned it to free.")
                return
            client_id = default_client_id(tg_id=str(target_tg_id))
            uri = build_rescue_uri_for_room(
                room=str(room["room_url"]),
                key_hex=key_hex,
                client_id=client_id,
            )
            await repo.mark_rescue_room_assigned(
                room_id=str(room["room_id"]),
                telegram_id=target_tg_id,
                session_id=session_id,
                key_hex=key_hex,
                client_id=client_id,
                uri=uri,
            )
            delivered = False
            try:
                await message.bot.send_message(
                    target_tg_id,
                    build_rescue_user_message(uri),
                    link_preview_options=NO_LINK_PREVIEW,
                )
                delivered = True
            except Exception:
                delivered = False
            await message.answer(
                "Warm Rescue room assigned.\n"
                f"room: {room['room_url']}\n"
                f"session: {session_id}\n"
                f"target: {target_tg_id}\n"
                "user_delivery: " + ("sent" if delivered else "not_sent"),
                link_preview_options=NO_LINK_PREVIEW,
                reply_markup=rescue_status_keyboard(session_id),
            )
            return

        try:
            session = create_local_session(room=str(room["room_url"]), tg_id=str(target_tg_id))
        except Exception as exc:
            await repo.mark_rescue_room_status(room_id=str(room["room_id"]), status="free")
            await message.answer(f"Failed to create local Rescue session: {exc}")
            return

        await message.answer(
            "Rescue room claimed from pool.\n"
            f"room: {session.room_url}\n"
            f"session: {session.session_id}\n"
            f"target: {target_tg_id}\n"
            f"Deploying on {deploy_host}..."
        )
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
        if not result.ok:
            await repo.mark_rescue_room_status(
                room_id=str(room["room_id"]),
                status="free",
                increment_fail_count=True,
            )
            for chunk in split_message(f"Rescue deploy failed at {result.failed_step}\n{result.output}", limit=3500):
                await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)
            return

        remote_root = str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue"))
        is_active, active_check_output = await wait_for_rescue_session_active(
            session_id=session.session_id,
            deploy_host=deploy_host,
            remote_root=remote_root,
            timeout_sec=max(5, int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60))),
        )
        if not is_active:
            await repo.mark_rescue_room_status(
                room_id=str(room["room_id"]),
                status="bad",
                session_id=session.session_id,
                telegram_id=target_tg_id,
                increment_fail_count=True,
            )
            await stop_rescue_session(
                session_id=session.session_id,
                deploy_host=deploy_host,
                timeout_sec=max(5, int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60))),
            )
            details = (
                "Rescue deploy started, but relay did not become active. User URI was not sent.\n"
                f"room: {session.room_url}\n"
                f"session: {session.session_id}\n"
                f"target: {target_tg_id}\n"
                "room marked: bad\n\n"
                "Last remote list:\n"
                f"{active_check_output}"
            )
            for chunk in split_message(details, limit=3500):
                await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)
            return

        await repo.mark_rescue_room_assigned(
            room_id=str(room["room_id"]),
            telegram_id=target_tg_id,
            session_id=session.session_id,
            key_hex=session.key_hex,
            client_id=session.client_id,
            uri=session.uri,
        )
        user_message = build_rescue_user_message(session.uri)
        delivered = False
        try:
            await message.bot.send_message(target_tg_id, user_message, link_preview_options=NO_LINK_PREVIEW)
            delivered = True
        except Exception:
            delivered = False

        admin_text = build_rescue_admin_summary(session, deploy_host=deploy_host)
        admin_text += f"\n\npool_room_id: {room['room_id']}"
        admin_text += "\n\nauto_deploy: ok"
        admin_text += "\n\nuser_delivery: " + ("sent" if delivered else "not_sent")
        for chunk in split_message(admin_text, limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)
        await message.answer(
            f"Статус сессии: {session.session_id}",
            reply_markup=rescue_status_keyboard(session.session_id),
        )
    @router.message(Command("rescue_status"))
    async def rescue_status_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Использование: /rescue_status <session_id>")
            return
        try:
            session_id = validate_session_id(parts[1].strip())
        except ValueError:
            await message.answer("Некорректный session_id.")
            return
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not deploy_host:
            await message.answer("OLCRTC_RESCUE_DEPLOY_HOST не настроен.")
            return
        await message.answer(f"Проверяю Rescue-сессию {session_id} на {deploy_host}...")
        result = await fetch_rescue_status(
            session_id=session_id,
            deploy_host=deploy_host,
            timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
        )
        prefix = "Rescue status: ok" if result.ok else f"Rescue status: failed at {result.failed_step}"
        diagnosis = diagnose_rescue_status_output(result.output)
        text = f"{prefix}\n{diagnosis}\n\n{result.output}" if diagnosis else f"{prefix}\n{result.output}"
        for chunk in split_message(text, limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)

    @router.message(Command("rescue_list"))
    async def rescue_list_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ.")
            return
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not deploy_host:
            await message.answer("OLCRTC_RESCUE_DEPLOY_HOST РЅРµ РЅР°СЃС‚СЂРѕРµРЅ.")
            return
        await message.answer(f"Checking Rescue sessions on {deploy_host}...")
        result = await fetch_rescue_list(
            deploy_host=deploy_host,
            remote_root=str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue")),
            timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
        )
        prefix = "Rescue list: ok" if result.ok else f"Rescue list: failed at {result.failed_step}"
        for chunk in split_message(f"{prefix}\n{result.output}", limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)

    @router.message(Command("rescue_dashboard"))
    async def rescue_dashboard_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ.")
            return
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not deploy_host:
            await message.answer("OLCRTC_RESCUE_DEPLOY_HOST РЅРµ РЅР°СЃС‚СЂРѕРµРЅ.")
            return
        await message.answer(f"Checking Rescue dashboard on {deploy_host}...")
        result = await fetch_rescue_list(
            deploy_host=deploy_host,
            remote_root=str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue")),
            timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
        )
        text = (
            format_rescue_dashboard(result.output, deploy_host=deploy_host)
            if result.ok
            else f"Rescue dashboard: failed at {result.failed_step}\n{result.output}"
        )
        for chunk in split_message(text, limit=3500):
            await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)

    @router.message(Command("rescue_stop"))
    async def rescue_stop_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РїСЂР°РІ.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Usage: /rescue_stop <session_id>")
            return
        try:
            session_id = validate_session_id(parts[1].strip())
        except ValueError:
            await message.answer("Invalid session_id.")
            return
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not deploy_host:
            await message.answer("OLCRTC_RESCUE_DEPLOY_HOST РЅРµ РЅР°СЃС‚СЂРѕРµРЅ.")
            return
        await message.answer(f"Stopping Rescue session {session_id} on {deploy_host}...")
        result = await stop_rescue_session(
            session_id=session_id,
            deploy_host=deploy_host,
            timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
        )
        prefix = "Rescue stop: ok" if result.ok else f"Rescue stop: failed at {result.failed_step}"
        for chunk in split_message(f"{prefix}\n{result.output}", limit=3500):
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
