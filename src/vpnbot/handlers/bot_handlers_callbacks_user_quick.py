from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.vpnbot.handlers.bot_handlers_callbacks_user_deps import UserCallbackDeps


def register_quick_callbacks(*, router: Router, deps: UserCallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    guard_callback_rate_limit = deps.guard_callback_rate_limit
    list_replaceable_devices = deps.list_replaceable_devices
    get_bot_username = deps.get_bot_username
    build_user_faq_text = deps.build_user_faq_text
    normalize_channel_url = deps.normalize_channel_url
    pending_issue = deps.pending_issue
    device_methods_keyboard = deps.device_methods_keyboard
    devices_replace_keyboard = deps.devices_replace_keyboard
    devices_rename_keyboard = deps.devices_rename_keyboard

    def _support_url() -> str | None:
        username = str(settings.support_username or "").strip().lstrip("@")
        if not username:
            return None
        return f"https://t.me/{username}"

    def _legal_menu_keyboard() -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="📜 Условия использования", callback_data="quick:legal_terms")],
            [InlineKeyboardButton(text="🔒 Конфиденциальность", callback_data="quick:legal_privacy")],
            [InlineKeyboardButton(text="💳 Оплата и возвраты", callback_data="quick:legal_refund")],
            [InlineKeyboardButton(text="🔁 Автопродление", callback_data="quick:legal_autorenew")],
        ]
        support_link = _support_url()
        if support_link:
            rows.append([InlineKeyboardButton(text="🆘 Поддержка", url=support_link)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _legal_terms_text() -> str:
        return (
            "<b>📜 Условия использования RootVPN</b>\n"
            "1) Сервис предоставляет цифровой доступ к VPN-подпискам и ссылкам через Telegram-бота.\n"
            "2) Оплата означает согласие с условиями сервиса.\n"
            "3) Доступ выдается после подтверждения платежа.\n"
            "4) Одна ссылка устройства предназначена для одного устройства.\n"
            "5) Сервис используется только в законных целях.\n"
            "6) Сервис предоставляется «как есть»."
        )

    def _legal_privacy_text() -> str:
        return (
            "<b>🔒 Конфиденциальность RootVPN</b>\n"
            "Мы храним только минимум данных для работы сервиса:\n"
            "• Telegram ID/username\n"
            "• служебные данные доступов и устройств\n"
            "• статусы и идентификаторы платежей\n\n"
            "Данные используются только для выдачи доступа, поддержки и стабильной работы."
        )

    def _legal_refund_text() -> str:
        return (
            "<b>💳 Оплата и возвраты</b>\n"
            "1) Услуга цифровая и считается оказанной после выдачи доступа.\n"
            "2) Возвраты обычно не предусмотрены.\n"
            "3) Исключение: подтвержденная техническая ошибка сервиса, "
            "когда оплаченный доступ не был выдан."
        )

    def _legal_autorenew_text() -> str:
        return (
            "<b>🔁 Автопродление</b>\n"
            "Автопродление в RootVPN — это автоматическое создание счета до окончания срока.\n"
            "Автоматических списаний без оплаты пользователем не происходит.\n"
            "Если счет не оплачен — доступ не продлевается."
        )

    @router.callback_query(F.data.startswith("quick:"))
    async def quick_action_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        action = callback.data.split(":", 1)[1].strip()
        tg_id = int(callback.from_user.id)

        if action == "device":
            row = await repo.get_user(tg_id)
            if not row:
                await callback.answer()
                await callback.message.answer("❗ Сначала получите подписку.")
                return
            devices = await repo.list_devices(tg_id)
            if settings.device_limit > 0 and len(devices) >= settings.device_limit:
                await callback.answer()
                await callback.message.answer("Лимит устройств уже исчерпан.")
                return
            if not await repo.has_paid_plan_payment(tg_id):
                await callback.answer()
                await callback.message.answer(
                    "📱 Доп. устройство доступно только после оплаты основного тарифа.\n"
                    "Сначала нажмите «Купить доступ».",
                )
                return
            await callback.answer()
            await callback.message.answer(
                f"📱 Доп. устройство: {settings.device_add_rub:.2f} RUB.\n"
                "Оплата добавляет только новый слот устройства.\n"
                f"Новое устройство получает +{max(0, int(settings.pay_days))} дней доступа.\n"
                "После оплаты устройство появится автоматически.\n"
                "Название можно задать через «Переименовать устройство».",
                reply_markup=device_methods_keyboard(settings),
            )
            return

        if action == "replace":
            devices = await list_replaceable_devices(tg_id)
            await callback.answer()
            if not devices:
                await callback.message.answer("Активные устройства не найдены. Сначала получите подписку.")
                return
            kb = devices_replace_keyboard(devices)
            await callback.message.answer(
                "Выберите устройство для перевыпуска ссылки.\n"
                "Старая ссылка выбранного устройства будет отключена.",
                reply_markup=kb,
            )
            return

        if action == "rename":
            devices = await repo.list_devices(tg_id)
            await callback.answer()
            if not devices:
                await callback.message.answer("Устройства не найдены. Сначала получите подписку.")
                return
            kb = devices_rename_keyboard(devices)
            await callback.message.answer("Выберите устройство для переименования:", reply_markup=kb)
            return

        if action == "ref":
            username = await get_bot_username(callback.message.bot)
            await callback.answer()
            if not username:
                await callback.message.answer("Не удалось получить username бота. Попробуйте позже.")
                return
            link = f"https://t.me/{username}?start=ref_{tg_id}"
            stats = await repo.get_referral_stats(tg_id)
            await callback.message.answer(
                "🎁 Реферальная программа:\n"
                f"- Бонус за оплаченного друга: +{max(0, settings.referral_bonus_days)} дн.\n"
                f"- Приглашено: {stats['total']}\n"
                f"- Бонус выдан: {stats['rewarded']}\n"
                f"- Ожидают первую оплату: {stats['pending']}\n\n"
                "Ваша ссылка:\n"
                f"{link}"
            )
            return

        if action == "faq":
            await callback.answer()
            await callback.message.answer(build_user_faq_text(), parse_mode="HTML")
            return

        if action == "legal":
            await callback.answer()
            support_link = _support_url()
            text = (
                "<b>📄 Правила и политика</b>\n"
                "Выберите нужный раздел ниже."
            )
            if support_link:
                text += f"\n\nПоддержка: {html.escape(support_link)}"
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=_legal_menu_keyboard(),
            )
            return

        if action == "legal_terms":
            await callback.answer()
            await callback.message.answer(
                _legal_terms_text(),
                parse_mode="HTML",
                reply_markup=_legal_menu_keyboard(),
            )
            return

        if action == "legal_privacy":
            await callback.answer()
            await callback.message.answer(
                _legal_privacy_text(),
                parse_mode="HTML",
                reply_markup=_legal_menu_keyboard(),
            )
            return

        if action == "legal_refund":
            await callback.answer()
            await callback.message.answer(
                _legal_refund_text(),
                parse_mode="HTML",
                reply_markup=_legal_menu_keyboard(),
            )
            return

        if action == "legal_autorenew":
            await callback.answer()
            await callback.message.answer(
                _legal_autorenew_text(),
                parse_mode="HTML",
                reply_markup=_legal_menu_keyboard(),
            )
            return

        if action == "channel":
            await callback.answer()
            link = normalize_channel_url(settings.channel_url)
            if link:
                await callback.message.answer(f"<b>📢 Наш канал</b>\n{link}", parse_mode="HTML")
            else:
                await callback.message.answer("Канал пока не настроен. Администратор скоро добавит ссылку.")
            return

        if action == "issue":
            pending_issue.add(tg_id)
            await callback.answer()
            await callback.message.answer(
                "Опишите проблему одним сообщением по шаблону:\n"
                "1) Время (дата и время по МСК)\n"
                "2) Устройство и приложение (iOS/Android/Windows + клиент)\n"
                "3) Что именно не работает\n"
                "4) Ошибка/скрин (если есть)\n"
                "5) Пробовали переимпорт/перезапуск\n\n"
                "Напишите «отмена» чтобы выйти."
            )
            return

        await callback.answer("Неизвестное действие", show_alert=True)
