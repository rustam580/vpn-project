"""User-facing command and button handlers extracted from build_router."""
from __future__ import annotations

import html
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app_texts import build_user_faq_text
from src.vpnbot.bot_access import ensure_device
from src.vpnbot.bot_formatters import (
    format_expire,
    format_last_online,
    format_used,
    plan_gb_text,
    plans_list_text,
)
from src.vpnbot.device_utils import (
    _device_label,
    normalize_device_name,
)
from src.vpnbot.env_utils import normalize_channel_url
from src.vpnbot.keyboards.bot_keyboards import (
    buy_plan_keyboard,
    buy_target_keyboard,
    device_methods_keyboard,
    devices_rename_keyboard,
    more_actions_keyboard,
)
from src.vpnbot.messaging import send_device_links, send_status
from src.vpnbot.message_utils import split_message
from src.vpnbot.messaging import NO_LINK_PREVIEW
from src.vpnbot.olcrtc_rescue import (
    active_rescue_session_ids,
    build_deploy_steps,
    build_rescue_user_message,
    build_rescue_uri_for_room,
    create_local_session,
    default_client_id,
    fetch_rescue_list,
    parse_rescue_list_output,
    parse_room_broker_output,
    run_room_broker,
    run_steps_async,
    stop_rescue_session,
    wait_for_rescue_session_active,
)
from src.vpnbot.services.bot_marzban import MarzbanClient
from src.vpnbot.db.bot_repo import Repo


@dataclass
class UserRuntimeDeps:
    """Dependencies for user-facing runtime handlers."""

    settings: Any
    repo: Repo
    marzban: MarzbanClient
    guard_message_rate_limit: Callable[[Message], Awaitable[bool]]
    list_replaceable_devices: Callable[[int], Awaitable[list[dict[str, Any]]]]
    get_bot_username: Callable[[Any], Awaitable[str]]
    track_event: Callable[..., Awaitable[None]]
    pending_issue: set[int]
    check_and_apply_payment: Callable[..., Awaitable[tuple[str, dict[str, Any] | None]]]
    enabled_payment_providers: Callable[[Any], list[str]]


def register_user_runtime_handlers(*, router: Router, deps: UserRuntimeDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_message_rate_limit = deps.guard_message_rate_limit
    list_replaceable_devices = deps.list_replaceable_devices
    get_bot_username = deps.get_bot_username
    track_event = deps.track_event
    pending_issue = deps.pending_issue
    check_and_apply_payment = deps.check_and_apply_payment
    enabled_payment_providers = deps.enabled_payment_providers

    async def _has_active_access(tg_id: int) -> bool:
        now = int(time.time())
        devices = await repo.list_devices(tg_id)
        users: list[dict[str, Any]] = []
        if devices:
            for row in devices:
                username = str(row.get("marzban_username") or "").strip()
                if not username:
                    continue
                user = await marzban.get_user(username)
                if user:
                    users.append(user)
        else:
            _, user, _ = await ensure_device(
                telegram_id=tg_id,
                device_id=1,
                repo=repo,
                marzban=marzban,
                settings=settings,
                create_if_missing=False,
            )
            if user:
                users.append(user)

        for user in users:
            if str(user.get("status", "unknown")) != "active":
                continue
            expire = int(user.get("expire", 0) or 0)
            if expire == 0 or expire > now:
                return True
        return False

    async def _create_rescue_room_on_demand(message: Message, target_tg_id: int) -> dict[str, Any] | None:
        broker_enabled = bool(getattr(settings, "olcrtc_rescue_room_broker_enabled", False))
        broker_command = str(getattr(settings, "olcrtc_rescue_room_broker_command", "") or "").strip()
        if not broker_enabled or not broker_command:
            await message.answer(
                "Rescue Beta пока не выдается автоматически. Напишите в поддержку, мы включим аварийный канал вручную."
            )
            return None

        broker_result = await run_room_broker(
            command_template=broker_command,
            count=1,
            timeout_sec=int(getattr(settings, "olcrtc_rescue_room_broker_timeout_sec", 45)),
        )
        if not broker_result.ok:
            await message.answer("Не удалось создать Rescue-комнату. Напишите в поддержку, мы выдадим ссылку вручную.")
            return None

        created_urls = parse_room_broker_output(broker_result.output)
        if not created_urls:
            await message.answer("WB Stream не вернул ссылку комнаты. Напишите в поддержку, мы проверим канал.")
            return None

        for room_url in created_urls:
            room_id = room_url.rsplit("/", 1)[-1]
            await repo.add_rescue_room(
                room_id=room_id,
                room_url=room_url,
                note="auto-created on demand by user",
            )

        return await repo.claim_next_free_rescue_room(
            telegram_id=target_tg_id,
            active_warm_session_ids=set(),
        )

    async def issue_rescue_beta(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        await track_event("rescue_beta_requested", telegram_id=tg_id)

        if not await _has_active_access(tg_id):
            await message.answer(
                "🆘 Rescue Beta доступен пользователям с активной подпиской.\n"
                "Сначала нажмите «🔑 Получить подписку» или «💳 Купить доступ»."
            )
            return

        auto_deploy_enabled = bool(getattr(settings, "olcrtc_rescue_auto_deploy", False))
        deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
        if not auto_deploy_enabled or not deploy_host:
            await message.answer("Rescue Beta сейчас на настройке. Если нужен аварийный доступ, напишите в поддержку.")
            return

        await message.answer(
            "🆘 Готовлю Rescue Beta.\n"
            "Создаю аварийный канал и проверяю подключение. Обычно это занимает 20-60 секунд."
        )

        remote_root = str(getattr(settings, "olcrtc_rescue_remote_root", "/etc/rootvpn/rescue"))
        deploy_timeout = max(5, int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)))
        active_warm_session_ids: set[str] = set()
        existing = await fetch_rescue_list(
            deploy_host=deploy_host,
            remote_root=remote_root,
            timeout_sec=deploy_timeout,
        )
        if existing.ok:
            active_warm_session_ids = active_rescue_session_ids(parse_rescue_list_output(existing.output))

        room = await repo.claim_next_free_rescue_room(
            telegram_id=tg_id,
            active_warm_session_ids=active_warm_session_ids,
        )
        if room is None:
            room = await _create_rescue_room_on_demand(message, tg_id)
            if room is None:
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
                await message.answer("Эта Rescue-комната уже недоступна. Попробуйте запросить Rescue Beta еще раз через минуту.")
                return

            client_id = default_client_id(tg_id=str(tg_id))
            uri = build_rescue_uri_for_room(
                room=str(room["room_url"]),
                key_hex=key_hex,
                client_id=client_id,
            )
            await repo.mark_rescue_room_assigned(
                room_id=str(room["room_id"]),
                telegram_id=tg_id,
                session_id=session_id,
                key_hex=key_hex,
                client_id=client_id,
                uri=uri,
            )
            await message.answer(build_rescue_user_message(uri), link_preview_options=NO_LINK_PREVIEW)
            await message.answer("Готово. Если не подключится с первого раза, нажмите START еще раз через 10 секунд.")
            await track_event("rescue_beta_delivered", telegram_id=tg_id, event_value=session_id)
            return

        try:
            session = create_local_session(room=str(room["room_url"]), tg_id=str(tg_id))
        except Exception:
            await repo.mark_rescue_room_status(room_id=str(room["room_id"]), status="free")
            await message.answer("Не удалось подготовить Rescue Beta. Попробуйте позже или напишите в поддержку.")
            return

        steps = build_deploy_steps(
            session_id=session.session_id,
            local_dir=session.out_dir,
            deploy_host=deploy_host,
            remote_root=remote_root,
            install_service=bool(getattr(settings, "olcrtc_rescue_install_service", True)),
            start_service=True,
            safe_ssh=True,
        )
        result = await run_steps_async(steps, timeout_sec=deploy_timeout)
        if not result.ok:
            await repo.mark_rescue_room_status(
                room_id=str(room["room_id"]),
                status="free",
                increment_fail_count=True,
            )
            await message.answer("Rescue-сервер не смог запуститься. Напишите в поддержку, мы проверим канал.")
            return

        is_active, active_check_output = await wait_for_rescue_session_active(
            session_id=session.session_id,
            deploy_host=deploy_host,
            remote_root=remote_root,
            timeout_sec=deploy_timeout,
        )
        if not is_active:
            await repo.mark_rescue_room_status(
                room_id=str(room["room_id"]),
                status="bad",
                session_id=session.session_id,
                telegram_id=tg_id,
                increment_fail_count=True,
            )
            await stop_rescue_session(
                session_id=session.session_id,
                deploy_host=deploy_host,
                timeout_sec=deploy_timeout,
            )
            details = (
                "Rescue-сервер не успел подключиться к комнате. Попробуйте запросить Rescue Beta еще раз через минуту.\n\n"
                "Технический статус:\n"
                f"{active_check_output}"
            )
            for chunk in split_message(details, limit=3500):
                await message.answer(chunk, link_preview_options=NO_LINK_PREVIEW)
            return

        await repo.mark_rescue_room_assigned(
            room_id=str(room["room_id"]),
            telegram_id=tg_id,
            session_id=session.session_id,
            key_hex=session.key_hex,
            client_id=session.client_id,
            uri=session.uri,
        )
        await message.answer(build_rescue_user_message(session.uri), link_preview_options=NO_LINK_PREVIEW)
        await message.answer("Готово. Если не подключится с первого раза, нажмите START еще раз через 10 секунд.")
        await track_event("rescue_beta_delivered", telegram_id=tg_id, event_value=session.session_id)

    @router.message(Command("config"))
    async def config_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        _, user, _ = await ensure_device(
            telegram_id=int(message.from_user.id),
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=False,
        )
        if not user:
            await message.answer("❗ Активный профиль не найден. Нажмите «🔑 Получить подписку».")
            return
        await send_status(message, user)
        await send_device_links(
            message=message,
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(Command("rescue_beta"))
    async def rescue_beta_cmd(message: Message) -> None:
        await issue_rescue_beta(message)

    @router.message(Command("diag"))
    async def diag_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        lines: list[str] = [f"🧪 Диагностика\nTG: {tg_id}"]

        devices = await repo.list_devices(tg_id)
        if not devices:
            lines.append("Активный профиль не найден. Нажмите «🔑 Получить подписку».")
            await message.answer("\n".join(lines))
            return

        lines.append("Устройства:")
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            username = str(row.get("marzban_username") or "").strip()
            mz_user = await marzban.get_user(username) if username else None
            if not mz_user:
                lines.append(f"- {device_id}. {label}: профиль не найден на сервере")
                continue
            status = str(mz_user.get("status", "unknown"))
            used = format_used(int(mz_user.get("used_traffic", 0) or 0))
            expire = format_expire(int(mz_user.get("expire", 0) or 0))
            online = format_last_online(
                mz_user.get("online_at") or mz_user.get("last_online") or mz_user.get("last_online_at")
            )
            lines.append(
                f"- {device_id}. {label}: {status}, онлайн: {online}, трафик: {used}, доступ до: {expire}"
            )

        latest_payment = await repo.get_latest_payment(tg_id)
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
                f"Последний платеж: {provider}, {purpose}, {amount:.2f} RUB, {status}, {updated_text}"
            )
        else:
            lines.append("Платежи: не найдено")

        lines.append("Если подключение не работает, нажмите «⚠️ Проблема с подключением».")
        await message.answer("\n".join(lines))

    @router.message(Command("buy"))
    async def buy_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            await message.answer(
                "💳 Выберите тариф для продления устройства 1:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=1),
            )
            return
        tg_id = int(message.from_user.id)
        devices = await repo.list_devices(tg_id)
        if not devices:
            await message.answer(
                "💳 Выберите тариф для продления основного ключа (устройство 1):\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=1),
            )
            return
        if len(devices) == 1:
            only_slot = int(devices[0]["device_id"])
            await message.answer(
                f"💳 Выберите тариф для продления устройства {only_slot}:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=only_slot),
            )
            return
        await message.answer(
            "💳 Выберите, что продлить:",
            reply_markup=buy_target_keyboard(devices),
        )

    @router.message(Command("device"))
    async def device_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        row = await repo.get_user(tg_id)
        if not row:
            await message.answer("❗ Сначала получите подписку.")
            return
        devices = await repo.list_devices(tg_id)
        if settings.device_limit > 0 and len(devices) >= settings.device_limit:
            await message.answer("Лимит устройств уже исчерпан.")
            return
        if not await repo.has_paid_plan_payment(tg_id):
            await message.answer(
                "📱 Дополнительное устройство доступно после оплаты основного тарифа.\n"
                "Сначала нажмите «Купить доступ»."
            )
            return
        await message.answer(
            f"📱 Дополнительное устройство: {settings.device_add_rub:.2f} RUB.\n"
            f"После оплаты появится новый слот на {max(0, int(settings.pay_days))} дн.\n"
            "Название можно задать через «Переименовать устройство».",
            reply_markup=device_methods_keyboard(settings),
        )

    @router.message(Command("replace"))
    async def replace_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        devices = await list_replaceable_devices(tg_id)
        if not devices:
            await message.answer("Активные устройства не найдены. Сначала получите подписку.")
            return
        from src.vpnbot.keyboards.bot_keyboards import devices_replace_keyboard
        kb = devices_replace_keyboard(devices)
        await message.answer(
            "Выберите устройство для перевыпуска ссылки.\n"
            "Старая ссылка выбранного устройства перестанет работать.",
            reply_markup=kb,
        )

    @router.message(Command("devices"))
    async def devices_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        devices = await repo.list_devices(int(message.from_user.id))
        if not devices:
            await message.answer("Устройства не найдены. Сначала получите подписку.")
            return
        lines: list[str] = []
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            if label.startswith("Устройство"):
                lines.append(f"{device_id}. {label}")
            else:
                lines.append(f"{device_id}. Устройство {device_id} — {label}")
        await message.answer("Ваши устройства:\n" + "\n".join(lines))

    @router.message(Command("device_name"))
    async def device_name_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Использование: /device_name <id> <имя устройства>")
            return
        try:
            device_id = int(parts[1])
        except ValueError:
            await message.answer("ID устройства должен быть числом. Пример: /device_name 2 Мой ноутбук")
            return
        if device_id < 1:
            await message.answer("ID устройства должен быть >= 1")
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await message.answer(f"ID устройства должен быть в диапазоне 1..{settings.device_limit}")
            return
        name = normalize_device_name(parts[2])
        if not name:
            await message.answer("Имя устройства не может быть пустым.")
            return
        row = await repo.get_device(int(message.from_user.id), device_id)
        if not row:
            await message.answer("Устройство не найдено. Сначала получите подписку.")
            return
        await repo.set_device_name(int(message.from_user.id), device_id, name)
        await message.answer(f"✅ Устройство {device_id} теперь называется: {name}")

    @router.message(Command("check"))
    async def check_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            providers = enabled_payment_providers(settings)
            await message.answer(
                "Использование: /check <" + "|".join(providers) + "> <payment_id>"
                if providers
                else "Провайдеры оплаты не настроены."
            )
            return
        provider = parts[1].lower().strip()
        allowed = enabled_payment_providers(settings)
        if provider not in set(allowed):
            if not allowed:
                await message.answer("Провайдеры оплаты не настроены.")
            else:
                await message.answer("Допустимые провайдеры: " + ", ".join(allowed))
            return
        result, updated = await check_and_apply_payment(
            provider=provider,
            external_id=parts[2],
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
            bot=message.bot,
        )
        await message.answer(result)
        if updated:
            await send_status(message, updated)
            await send_device_links(
                message=message,
                telegram_id=int(message.from_user.id),
                repo=repo,
                marzban=marzban,
                settings=settings,
            )

    @router.message(F.text.in_({"🔑 Получить конфиг", "🔑 Получить подписку"}))
    async def get_config(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        await track_event("config_requested", telegram_id=tg_id)
        _, user, created = await ensure_device(
            telegram_id=tg_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=True,
        )
        if created:
            await message.answer(
                f"🎁 Тестовый доступ выдан: {settings.trial_days} дн., {plan_gb_text(settings.trial_gb)}."
            )
            await track_event("trial_issued", telegram_id=tg_id)
        await send_status(message, user or {})
        await send_device_links(
            message=message,
            telegram_id=tg_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(F.text == "📊 Мой статус")
    async def status_cmd(message: Message) -> None:
        await config_cmd(message)

    @router.message(F.text == "🆘 Rescue Beta")
    async def rescue_beta_btn(message: Message) -> None:
        await issue_rescue_beta(message)

    @router.message(F.text == "💳 Купить доступ")
    async def buy_btn(message: Message) -> None:
        await buy_cmd(message)

    @router.message(F.text == "📂 Еще")
    async def more_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(
            "<b>Дополнительные действия</b>\n"
            "Выберите, что нужно сделать:",
            reply_markup=more_actions_keyboard(),
            parse_mode="HTML",
        )

    @router.message(F.text == "📱 Добавить устройство")
    async def device_btn(message: Message) -> None:
        await device_cmd(message)

    @router.message(F.text == "🔁 Заменить устройство")
    async def replace_btn(message: Message) -> None:
        await replace_cmd(message)

    @router.message(F.text == "✏️ Переименовать устройство")
    async def device_rename_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        devices = await repo.list_devices(int(message.from_user.id))
        if not devices:
            await message.answer("Устройства не найдены. Сначала получите подписку.")
            return
        kb = devices_rename_keyboard(devices)
        await message.answer("Выберите устройство для переименования:", reply_markup=kb)

    @router.message(F.text == "🎁 Рефералка")
    async def ref_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        username = await get_bot_username(message.bot)
        if not username:
            await message.answer("Не удалось получить username бота. Попробуйте позже.")
            return
        link = f"https://t.me/{username}?start=ref_{tg_id}"
        stats = await repo.get_referral_stats(tg_id)
        await message.answer(
            "🎁 Реферальная программа:\n"
            f"- Бонус за оплаченного друга: +{max(0, settings.referral_bonus_days)} дн.\n"
            f"- Приглашено: {stats['total']}\n"
            f"- Бонус выдан: {stats['rewarded']}\n"
            f"- Ожидают первую оплату: {stats['pending']}\n\n"
            "Ваша реферальная ссылка:\n"
            f"{link}"
        )

    @router.message(F.text == "❓ FAQ")
    async def faq_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(build_user_faq_text(), parse_mode="HTML")

    @router.message(F.text == "🆘 Поддержка")
    async def support_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            await track_event("support_opened", telegram_id=tg_id)
        safe_support_text = html.escape(settings.support_text)
        if settings.support_username:
            await message.answer(
                "<b>🆘 Поддержка</b>\n"
                f"{safe_support_text}\n\n"
                f"Контакт: https://t.me/{settings.support_username}",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "<b>🆘 Поддержка</b>\n"
                f"{safe_support_text}\n\n"
                "Контакт поддержки пока не задан администратором.",
                parse_mode="HTML",
            )

    @router.message(F.text == "📢 Наш канал")
    async def channel_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            await track_event("channel_opened", telegram_id=tg_id)
        link = normalize_channel_url(settings.channel_url)
        if link:
            await message.answer(f"<b>📢 Наш канал</b>\n{link}", parse_mode="HTML")
            return
        await message.answer("Канал пока не настроен. Администратор скоро добавит ссылку.")

    @router.message(F.text == "⚠️ Проблема с подключением")
    async def issue_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        pending_issue.add(tg_id)
        await message.answer(
            "Опишите проблему одним сообщением:\n"
            "1. Когда началось.\n"
            "2. Устройство и приложение.\n"
            "3. Что именно не работает.\n"
            "4. Ошибка или скрин, если есть.\n"
            "5. Пробовали ли переимпорт или перезапуск.\n\n"
            "Напишите «отмена», чтобы выйти."
        )
