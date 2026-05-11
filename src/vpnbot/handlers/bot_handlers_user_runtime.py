"""User-facing command and button handlers extracted from build_router."""
from __future__ import annotations

import html
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
            await message.answer("❗ Профиль не найден. Нажмите «🔑 Получить подписку».")
            return
        await send_status(message, user)
        await send_device_links(
            message=message,
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

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
            lines.append("Профиль не найден. Нажмите «🔑 Получить подписку».")
            await message.answer("\n".join(lines))
            return

        lines.append("Устройства:")
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            username = str(row.get("marzban_username") or "").strip()
            mz_user = await marzban.get_user(username) if username else None
            if not mz_user:
                lines.append(f"- {device_id}. {label}: не найдено в Marzban")
                continue
            status = str(mz_user.get("status", "unknown"))
            used = format_used(int(mz_user.get("used_traffic", 0) or 0))
            expire = format_expire(int(mz_user.get("expire", 0) or 0))
            online = format_last_online(
                mz_user.get("online_at") or mz_user.get("last_online") or mz_user.get("last_online_at")
            )
            lines.append(
                f"- {device_id}. {label}: {status}, онлайн: {online}, трафик: {used}, до: {expire}"
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

        lines.append("Если есть проблемы, отправьте «⚠️ Проблема с подключением».")
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
            await message.answer("❗ Сначала получите ссылку подписки.")
            return
        devices = await repo.list_devices(tg_id)
        if settings.device_limit > 0 and len(devices) >= settings.device_limit:
            await message.answer("Лимит устройств уже исчерпан.")
            return
        if not await repo.has_paid_plan_payment(tg_id):
            await message.answer(
                "📱 Доп. устройство доступно только после оплаты основного тарифа.\n"
                "Сначала нажмите «Купить доступ»."
            )
            return
        await message.answer(
            f"📱 Доп. устройство: {settings.device_add_rub:.2f} RUB.\n"
            "Оплата добавляет только новый слот устройства.\n"
            f"Новое устройство получает +{max(0, int(settings.pay_days))} дней доступа.\n"
            "После оплаты устройство появится автоматически.\n"
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
            "Старая ссылка выбранного устройства будет отключена.",
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
                f"🎁 Тестовый доступ выдан: {settings.trial_days} день, {plan_gb_text(settings.trial_gb)}."
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

    @router.message(F.text == "💳 Купить доступ")
    async def buy_btn(message: Message) -> None:
        await buy_cmd(message)

    @router.message(F.text == "📂 Еще")
    async def more_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(
            "<b>Дополнительные действия</b>\n"
            "Выберите нужный пункт:",
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
            "Ваша ссылка:\n"
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
            "Опишите проблему одним сообщением по шаблону:\n"
            "1) Время (дата и время по МСК)\n"
            "2) Устройство и приложение (iOS/Android/Windows + клиент)\n"
            "3) Что именно не работает\n"
            "4) Ошибка/скрин (если есть)\n"
            "5) Пробовали переимпорт/перезапуск\n\n"
            "Напишите «отмена» чтобы выйти."
        )
