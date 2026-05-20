from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message


@dataclass
class UserMessageDeps:
    settings: Any
    repo: Any
    guard_message_rate_limit: Any
    extract_start_payload: Any
    parse_referrer_from_payload: Any
    parse_web_order_from_payload: Any
    bind_web_order_fn: Any
    build_start_text: Any
    plan_gb_text: Any
    format_device_limit: Any
    keyboard_for_user: Any
    is_admin_fn: Any
    track_event: Any
    bot_token: str
    enabled_payment_providers: Any
    get_bot_username: Any
    build_user_faq_text: Any
    quick_connect_guide_text: Any
    normalize_channel_url: Any


def register_user_message_handlers(*, router: Router, deps: UserMessageDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    guard_message_rate_limit = deps.guard_message_rate_limit
    extract_start_payload = deps.extract_start_payload
    parse_referrer_from_payload = deps.parse_referrer_from_payload
    parse_web_order_from_payload = deps.parse_web_order_from_payload
    bind_web_order_fn = deps.bind_web_order_fn
    build_start_text = deps.build_start_text
    plan_gb_text = deps.plan_gb_text
    format_device_limit = deps.format_device_limit
    keyboard_for_user = deps.keyboard_for_user
    is_admin_fn = deps.is_admin_fn
    track_event = deps.track_event
    bot_token = deps.bot_token
    enabled_payment_providers = deps.enabled_payment_providers
    get_bot_username = deps.get_bot_username
    build_user_faq_text = deps.build_user_faq_text
    quick_connect_guide_text = deps.quick_connect_guide_text
    normalize_channel_url = deps.normalize_channel_url

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            payload = extract_start_payload(message.text)
            web_order_id = parse_web_order_from_payload(payload, bot_token=bot_token)
            if web_order_id:
                ok, bind_msg = await bind_web_order_fn(
                    telegram_id=tg_id,
                    order_id=web_order_id,
                )
                await message.answer(bind_msg)
                if ok:
                    await track_event(
                        "web_order_bind_start_ok",
                        telegram_id=tg_id,
                        event_meta={"order_id": web_order_id},
                    )
                else:
                    await track_event(
                        "web_order_bind_start_failed",
                        telegram_id=tg_id,
                        event_meta={"order_id": web_order_id},
                    )

            referrer_id = parse_referrer_from_payload(payload)
            if referrer_id is not None:
                bind_result = await repo.bind_referrer(
                    invited_telegram_id=tg_id,
                    referrer_telegram_id=referrer_id,
                )
                if bind_result == "bound":
                    await message.answer("Реферальная привязка сохранена. Бонус начислим после вашей первой оплаты.")
                elif bind_result == "self":
                    await message.answer("Нельзя указать себя как реферера.")

        await message.answer(
            build_start_text(
                trial_days=settings.trial_days,
                trial_gb_text=plan_gb_text(settings.trial_gb),
                pay_days=settings.pay_days,
                pay_gb_text=plan_gb_text(settings.pay_gb),
                pay_rub=settings.pay_rub,
                device_limit_text=format_device_limit(settings.device_limit),
            ),
            reply_markup=keyboard_for_user(is_admin=is_admin_fn(tg_id, settings)),
            parse_mode="HTML",
        )
        if tg_id is not None:
            await track_event("user_start", telegram_id=tg_id)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        providers = enabled_payment_providers(settings)
        check_hint = (
            "<code>/check &lt;" + "|".join(providers) + "&gt; &lt;payment_id&gt;</code> — проверить оплату"
            if providers
            else "<code>/check</code> — провайдеры оплаты не настроены"
        )
        is_admin_user = bool(message.from_user and is_admin_fn(int(message.from_user.id), settings))

        user_block = (
            "<b>👤 Команды пользователя</b>\n"
            "• <code>/config</code> — получить/обновить подписку\n"
            "• <code>/guide</code> — инструкция по подключению\n"
            "• <code>/diag</code> — диагностика подключения\n"
            "• <code>/rescue_beta</code> — аварийный режим для белых списков\n"
            "• <code>/buy</code> — продлить доступ\n"
            "• <code>/replace</code> — перевыпустить ссылку устройства\n"
            "• <code>/ref</code> — реферальная ссылка\n"
            f"• {check_hint}\n"
            "• <code>/faq</code> — частые вопросы\n"
            "• <code>/support</code> — поддержка\n"
            "• <code>/channel</code> — наш канал"
        )

        if not is_admin_user:
            await message.answer(user_block, parse_mode="HTML")
            return

        admin_block = (
            "<b>🛠 Команды администратора</b>\n"
            "• <code>/admin</code> — админ-кабинет\n"
            "• <code>/admin_stats</code> — краткая статистика\n"
            "• <code>/ref_stats [telegram_id]</code> — реф-статистика\n"
            "• <code>/ref_grant &lt;telegram_id&gt; [days]</code> — реф-бонус вручную\n"
            "• <code>/grant &lt;telegram_id&gt; &lt;days&gt; &lt;gb&gt;</code> — доступ всем слотам\n"
            "• <code>/grant_perm &lt;telegram_id&gt; [gb]</code> — бессрочный доступ\n"
            "• <code>/grant_device &lt;telegram_id&gt; &lt;slot&gt; &lt;days&gt; &lt;gb&gt;</code>\n"
            "• <code>/sync_expire &lt;telegram_id&gt; [max|min|slot:&lt;id&gt;]</code>\n"
            "• <code>/device_add &lt;telegram_id&gt; [slot]</code>\n"
            "• <code>/device_replace &lt;telegram_id&gt; &lt;slot&gt;</code>\n"
            "• <code>/disable &lt;telegram_id&gt;</code>\n"
            "• <code>/link &lt;telegram_id&gt; &lt;marzban_username&gt;</code>\n"
            "• <code>/ops</code> — health-отчет\n"
            "• <code>/payment_issues</code> — проблемные оплаты и выдача\n"
            "• <code>/sync_audit</code> — Marzban/DB рассинхрон\n"
            "• <code>/xray_errors [minutes]</code> — ошибки Xray\n"
            "• <code>/rescue &lt;telegram_id&gt; &lt;wb_room_url&gt;</code> — Rescue Beta\n"
            "• <code>/rescue_room_add &lt;wb_room_url&gt; [note]</code> — добавить WB-комнату\n"
            "• <code>/rescue_room_warm &lt;room_id_or_wb_room_url&gt;</code> — прогреть комнату\n"
            "• <code>/rescue_rooms</code> — пул WB-комнат\n"
            "• <code>/rescue_reconcile [apply]</code> — сверить пул с Rescue VPS\n"
            "• <code>/rescue_create &lt;telegram_id&gt;</code> — выдать Rescue из пула\n"
            "• <code>/rescue_dashboard</code> — панель Rescue\n"
            "• <code>/rescue_status &lt;session_id&gt;</code> — статус Rescue\n"
            "• <code>/rescue_list</code> — список Rescue-сессий\n"
            "• <code>/rescue_stop &lt;session_id&gt;</code> — остановить Rescue"
        )
        await message.answer(user_block + "\n\n" + admin_block, parse_mode="HTML")

    @router.message(Command("ref"))
    async def ref_cmd(message: Message) -> None:
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

    @router.message(Command("faq"))
    async def faq_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(build_user_faq_text(), parse_mode="HTML")

    @router.message(Command("guide"))
    async def guide_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(quick_connect_guide_text(), parse_mode="HTML")

    @router.message(Command("support"))
    async def support_cmd(message: Message) -> None:
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

    @router.message(Command("channel"))
    async def channel_cmd(message: Message) -> None:
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

    @router.message(Command("menu"))
    async def menu_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        await message.answer(
            "Меню обновлено.",
            reply_markup=keyboard_for_user(is_admin=is_admin_fn(tg_id, settings)),
        )
