from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.vpnbot.handlers.bot_handlers_callbacks_user_deps import UserCallbackDeps


def register_payment_callbacks(*, router: Router, deps: UserCallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_callback_rate_limit = deps.guard_callback_rate_limit
    send_status = deps.send_status
    send_device_links = deps.send_device_links
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
                f"Срок: +{plan.days} дн., трафик: {plan_gb_text(plan.gb)}.\n"
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
                f"Срок: +{plan.days} дн., трафик: {plan_gb_text(plan.gb)}.\n"
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
                f"Период: +{plan.days} дн., трафик: {plan_gb_text(plan.gb)}\n"
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
                f"Новое устройство получит +{device_days} дн. доступа.",
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
