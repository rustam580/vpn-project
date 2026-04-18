from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class UserCallbackDeps:
    settings: Any
    repo: Any
    marzban: Any
    guard_callback_rate_limit: Any
    list_replaceable_devices: Any
    get_bot_username: Any
    build_user_faq_text: Any
    normalize_channel_url: Any
    pending_issue: set[int]
    pending_device_rename: dict[int, int]
    replace_device_slot: Any
    send_status: Any
    send_device_links: Any
    collect_device_links: Any
    send_configs_in_chat: Any
    render_config_block: Any
    plans_list_text: Any
    buy_plan_keyboard: Any
    find_plan: Any
    plan_title: Any
    plan_gb_text: Any
    payment_methods_keyboard: Any
    cryptobot_create_invoice: Any
    yookassa_create_payment: Any
    track_event: Any
    pay_action_keyboard: Any
    next_device_slot: Any
    check_and_apply_payment: Any
    device_methods_keyboard: Any
    devices_replace_keyboard: Any
    devices_rename_keyboard: Any
    device_replace_confirm_keyboard: Any
    device_label: Any


def register_user_callback_handlers(*, router: Router, deps: UserCallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_callback_rate_limit = deps.guard_callback_rate_limit
    list_replaceable_devices = deps.list_replaceable_devices
    get_bot_username = deps.get_bot_username
    build_user_faq_text = deps.build_user_faq_text
    normalize_channel_url = deps.normalize_channel_url
    pending_issue = deps.pending_issue
    pending_device_rename = deps.pending_device_rename
    replace_device_slot = deps.replace_device_slot
    send_status = deps.send_status
    send_device_links = deps.send_device_links
    collect_device_links = deps.collect_device_links
    send_configs_in_chat = deps.send_configs_in_chat
    render_config_block = deps.render_config_block
    plans_list_text = deps.plans_list_text
    buy_plan_keyboard = deps.buy_plan_keyboard
    find_plan = deps.find_plan
    plan_title = deps.plan_title
    plan_gb_text = deps.plan_gb_text
    payment_methods_keyboard = deps.payment_methods_keyboard
    cryptobot_create_invoice = deps.cryptobot_create_invoice
    yookassa_create_payment = deps.yookassa_create_payment
    track_event = deps.track_event
    pay_action_keyboard = deps.pay_action_keyboard
    next_device_slot = deps.next_device_slot
    check_and_apply_payment = deps.check_and_apply_payment
    device_methods_keyboard = deps.device_methods_keyboard
    devices_replace_keyboard = deps.devices_replace_keyboard
    devices_rename_keyboard = deps.devices_rename_keyboard
    device_replace_confirm_keyboard = deps.device_replace_confirm_keyboard
    device_label = deps.device_label

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
                disable_web_page_preview=True,
            )
            await callback.answer()
            return
        await callback.answer("Неверный callback", show_alert=True)

    @router.callback_query(F.data.startswith("buyselect:"))
    async def buy_select_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        tg_id = int(callback.from_user.id)
        devices = await repo.list_devices(tg_id)
        parts = callback.data.split(":")
        if len(parts) < 2:
            await callback.answer("Неверный формат", show_alert=True)
            return
        if parts[1] == "all":
            if not devices:
                await callback.answer("Сначала получите подписку.", show_alert=True)
                return
            await callback.message.answer(
                f"🧩 Продление всех ключей ({len(devices)} шт).\n"
                "Выберите тариф:\n"
                + plans_list_text(settings, multiplier=len(devices)),
                reply_markup=buy_plan_keyboard(
                    settings,
                    target="all",
                    devices_count=len(devices),
                ),
            )
            await callback.answer()
            return
        if parts[1] == "slot" and len(parts) >= 3:
            try:
                slot = int(parts[2])
            except ValueError:
                await callback.answer("Неверный слот", show_alert=True)
                return
            row = await repo.get_device(tg_id, slot)
            if not row:
                await callback.answer("Слот не найден", show_alert=True)
                return
            await callback.message.answer(
                f"🔑 Продление устройства {slot}.\n"
                "Выберите тариф:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=slot),
            )
            await callback.answer()
            return
        await callback.answer("Неверный формат", show_alert=True)

    @router.callback_query(F.data.startswith("buyplan:"))
    async def buy_plan_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Неверный формат", show_alert=True)
            return

        tg_id = int(callback.from_user.id)
        plan = find_plan(settings, parts[1])
        if plan is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        target = parts[2]
        if target == "all":
            devices = await repo.list_devices(tg_id)
            if not devices:
                await callback.answer("Сначала получите подписку.", show_alert=True)
                return
            amount = plan.rub * len(devices)
            await callback.message.answer(
                f"🧩 {plan_title(plan)} для всех устройств ({len(devices)} шт).\n"
                f"Сумма: {amount:.2f} RUB\n"
                f"Срок: +{plan.days} дней, трафик: {plan_gb_text(plan.gb)}.\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(
                    settings,
                    plan_key=plan.key,
                    target="all",
                ),
            )
            await callback.answer()
            return

        if target == "slot" and len(parts) >= 4:
            try:
                slot = int(parts[3])
            except ValueError:
                await callback.answer("Неверный слот", show_alert=True)
                return
            row = await repo.get_device(tg_id, slot)
            if not row and slot > 1:
                await callback.answer("Слот не найден", show_alert=True)
                return
            await callback.message.answer(
                f"🔑 {plan_title(plan)} для устройства {slot}.\n"
                f"Сумма: {plan.rub:.2f} RUB\n"
                f"Срок: +{plan.days} дней, трафик: {plan_gb_text(plan.gb)}.\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(
                    settings,
                    plan_key=plan.key,
                    target="slot",
                    device_id=slot,
                ),
            )
            await callback.answer()
            return

        await callback.answer("Неверный формат", show_alert=True)

    @router.callback_query(F.data.startswith("buy:"))
    async def buy_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        provider = parts[1] if len(parts) >= 2 else ""
        tg_id = int(callback.from_user.id)
        try:
            plan = settings.plans[0]
            suffix_idx = 2
            if len(parts) >= 5 and parts[2] == "plan":
                custom_plan = find_plan(settings, parts[3])
                if custom_plan is None:
                    await callback.answer("Тариф не найден", show_alert=True)
                    return
                plan = custom_plan
                suffix_idx = 4

            target = "slot"
            slot = 1
            if len(parts) > suffix_idx:
                suffix = parts[suffix_idx]
                if suffix == "all":
                    target = "all"
                elif suffix == "slot" and len(parts) > suffix_idx + 1:
                    try:
                        slot = int(parts[suffix_idx + 1])
                    except ValueError:
                        await callback.answer("Неверный слот", show_alert=True)
                        return

            if target == "slot":
                if slot < 1:
                    await callback.answer("Неверный слот", show_alert=True)
                    return
                if slot > 1:
                    slot_row = await repo.get_device(tg_id, slot)
                    if not slot_row:
                        await callback.answer("Слот не найден", show_alert=True)
                        return
                amount_rub = plan.rub
                purpose = "plan_device"
                device_slot = slot
                pay_desc = f"VPN {plan_title(plan)}: продление устройства {slot}, +{plan.days}d"
                pay_title = f"{plan_title(plan)} / устройство {slot}"
            else:
                devices = await repo.list_devices(tg_id)
                if not devices:
                    await callback.answer("Сначала получите подписку.", show_alert=True)
                    return
                amount_rub = plan.rub * len(devices)
                purpose = "plan_all"
                device_slot = 0
                pay_desc = f"VPN {plan_title(plan)}: продление всех устройств ({len(devices)} шт), +{plan.days}d"
                pay_title = f"{plan_title(plan)} / все устройства ({len(devices)} шт)"

            if provider == "crypto":
                if not settings.cryptobot_enabled():
                    await callback.answer("CryptoBot не настроен", show_alert=True)
                    return
                external_id, pay_url = await cryptobot_create_invoice(
                    settings,
                    tg_id,
                    amount_rub=amount_rub,
                    description=pay_desc,
                )
            elif provider == "card":
                if not settings.yookassa_enabled():
                    await callback.answer("YooKassa не настроена", show_alert=True)
                    return
                external_id, pay_url = await yookassa_create_payment(
                    settings,
                    tg_id,
                    amount_rub=amount_rub,
                    description=pay_desc,
                )
            else:
                await callback.answer("Неизвестный метод", show_alert=True)
                return

            await repo.upsert_payment(
                provider=provider,
                external_id=external_id,
                telegram_id=tg_id,
                days=plan.days,
                gb=plan.gb,
                amount_rub=amount_rub,
                pay_url=pay_url,
                status="pending",
                purpose=purpose,
                device_slot=device_slot,
            )
            await track_event(
                "payment_created_plan",
                telegram_id=tg_id,
                event_value=provider,
                event_meta={
                    "external_id": external_id,
                    "amount_rub": amount_rub,
                    "purpose": purpose,
                    "device_slot": device_slot,
                    "plan_key": plan.key,
                },
            )
            await callback.message.answer(
                f"✅ Платеж создан ({provider}).\n"
                f"Тип: {pay_title}\n"
                f"Сумма: {amount_rub:.2f} RUB\n"
                f"Период: +{plan.days} дней, трафик: {plan_gb_text(plan.gb)}\n"
                f"ID: {external_id}",
                reply_markup=pay_action_keyboard(provider, external_id, pay_url),
            )
            await callback.answer()
        except Exception as exc:
            logging.exception("Create payment failed")
            await callback.answer(f"Ошибка: {exc}", show_alert=True)

    @router.callback_query(F.data.startswith("device:"))
    async def device_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        provider = callback.data.split(":", 1)[1]
        tg_id = int(callback.from_user.id)
        try:
            devices = await repo.list_devices(tg_id)
            if settings.device_limit > 0 and len(devices) >= settings.device_limit:
                await callback.answer("Лимит устройств исчерпан", show_alert=True)
                return
            if not await repo.has_paid_plan_payment(tg_id):
                await callback.answer(
                    "Сначала оплатите основной тариф, затем добавляйте устройства.",
                    show_alert=True,
                )
                return
            used_slots = {int(d["device_id"]) for d in devices}
            slot = next_device_slot(used_slots, settings.device_limit)
            if slot is None:
                await callback.answer("Нет свободных слотов", show_alert=True)
                return

            device_days = max(0, int(getattr(settings, "pay_days", 30) or 0))
            device_gb = int(getattr(settings, "pay_gb", 0) or 0)

            if provider == "crypto":
                if not settings.cryptobot_enabled():
                    await callback.answer("CryptoBot не настроен", show_alert=True)
                    return
                external_id, pay_url = await cryptobot_create_invoice(
                    settings,
                    tg_id,
                    amount_rub=settings.device_add_rub,
                    description=f"VPN добавление устройства {slot}",
                )
            elif provider == "card":
                if not settings.yookassa_enabled():
                    await callback.answer("YooKassa не настроена", show_alert=True)
                    return
                external_id, pay_url = await yookassa_create_payment(
                    settings,
                    tg_id,
                    amount_rub=settings.device_add_rub,
                    description=f"VPN добавление устройства {slot}",
                )
            else:
                await callback.answer("Неизвестный метод", show_alert=True)
                return

            await repo.upsert_payment(
                provider=provider,
                external_id=external_id,
                telegram_id=tg_id,
                days=device_days,
                gb=device_gb,
                amount_rub=settings.device_add_rub,
                pay_url=pay_url,
                status="pending",
                purpose="device_add",
                device_slot=slot,
            )
            await track_event(
                "payment_created_device",
                telegram_id=tg_id,
                event_value=provider,
                event_meta={
                    "external_id": external_id,
                    "slot": slot,
                    "amount_rub": settings.device_add_rub,
                },
            )
            await callback.message.answer(
                f"✅ Платеж за устройство создан ({provider}).\n"
                f"ID: {external_id}\n"
                f"Слот: {slot}\n"
                f"Новое устройство получит +{device_days} дней доступа.\n"
                "Важно: одна ссылка = одно устройство.",
                reply_markup=pay_action_keyboard(provider, external_id, pay_url),
            )
            await callback.answer()
        except Exception as exc:
            logging.exception("Device payment create failed")
            await callback.answer(f"Ошибка: {exc}", show_alert=True)

    @router.callback_query(F.data.startswith("check:"))
    async def check_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Неверный callback", show_alert=True)
            return
        _, provider, external_id = parts
        try:
            result, updated = await check_and_apply_payment(
                provider=provider,
                external_id=external_id,
                telegram_id=int(callback.from_user.id),
                repo=repo,
                marzban=marzban,
                settings=settings,
                bot=callback.bot,
            )
            await callback.message.answer(result)
            if updated:
                await send_status(callback.message, updated)
                await send_device_links(
                    message=callback.message,
                    telegram_id=int(callback.from_user.id),
                    repo=repo,
                    marzban=marzban,
                    settings=settings,
                )
            await callback.answer("Готово")
        except Exception as exc:
            logging.exception("Check payment failed")
            await callback.answer(f"Ошибка: {exc}", show_alert=True)
