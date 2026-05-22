from __future__ import annotations

from typing import Any

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from src.vpnbot.bot_formatters import plan_title
from src.vpnbot.device_utils import _device_label, _short_label


def keyboard_for_user(*, is_admin: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="🔑 Получить подписку"), KeyboardButton(text="💳 Купить доступ")],
        [KeyboardButton(text="📊 Мой статус"), KeyboardButton(text="📂 Еще")],
        [KeyboardButton(text="🆘 Аварийный доступ"), KeyboardButton(text="🆘 Поддержка")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Админ-кабинет")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def more_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📱 Добавить устройство", callback_data="quick:device"),
                InlineKeyboardButton(text="♻️ Заменить устройство", callback_data="quick:replace"),
            ],
            [
                InlineKeyboardButton(text="✍️ Переименовать устройство", callback_data="quick:rename"),
                InlineKeyboardButton(text="🎁 Рефералка", callback_data="quick:ref"),
            ],
            [
                InlineKeyboardButton(text="❓ FAQ", callback_data="quick:faq"),
                InlineKeyboardButton(text="📢 Наш канал", callback_data="quick:channel"),
            ],
            [InlineKeyboardButton(text="🆘 Аварийный доступ", switch_inline_query_current_chat="/rescue_beta")],
            [InlineKeyboardButton(text="📄 Правила и политика", callback_data="quick:legal")],
            [InlineKeyboardButton(text="🚨 Проблема с подключением", callback_data="quick:issue")],
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="💳 Проблемные оплаты", callback_data="admin:payment_issues")],
            [InlineKeyboardButton(text="💼 Тарифы", callback_data="admin:plans")],
            [InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="admin:ref_top")],
            [InlineKeyboardButton(text="🩺 Ops отчет", callback_data="admin:ops")],
            [InlineKeyboardButton(text="📡 Xray ошибки", callback_data="admin:xray_errors")],
            [InlineKeyboardButton(text="🧭 Marzban inbounds", callback_data="admin:marzban_inbounds")],
            [InlineKeyboardButton(text="🧭 Marzban/DB аудит", callback_data="admin:sync_audit")],
            [InlineKeyboardButton(text="🆘 Rescue Dashboard", callback_data="admin:rescue_dashboard")],
            [InlineKeyboardButton(text="🧹 Rescue Reconcile", callback_data="admin:rescue_reconcile")],
            [InlineKeyboardButton(text="🏊 Rescue Room Pool", switch_inline_query_current_chat="/rescue_rooms")],
            [InlineKeyboardButton(text="🔥 Warm Rescue Room", switch_inline_query_current_chat="/rescue_room_warm ")],
            [InlineKeyboardButton(text="⚡ Rescue из пула", switch_inline_query_current_chat="/rescue_create ")],
            [
                InlineKeyboardButton(
                    text="🆘 Ручная Rescue",
                    switch_inline_query_current_chat="/rescue ",
                )
            ],
            [InlineKeyboardButton(text="🚀 Обновить и проверить", callback_data="admin:deploy")],
            [InlineKeyboardButton(text="🔎 Найти пользователя", callback_data="admin:find_user")],
            [InlineKeyboardButton(text="➕ Устройство", callback_data="admin:device_add")],
            [
                InlineKeyboardButton(
                    text="♻️ Заменить устройство",
                    switch_inline_query_current_chat="/device_replace ",
                )
            ],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
            [
                InlineKeyboardButton(
                    text="🎟 Выдать доступ",
                    switch_inline_query_current_chat="/grant ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💸 Проверить платеж",
                    switch_inline_query_current_chat="/check ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Отключить доступ",
                    switch_inline_query_current_chat="/disable ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Привязать аккаунт",
                    switch_inline_query_current_chat="/link ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Реф-бонус вручную",
                    switch_inline_query_current_chat="/ref_grant ",
                )
            ],
            [InlineKeyboardButton(text="🧰 Шаблоны поддержки", callback_data="admin:support_templates")],
            [InlineKeyboardButton(text="📚 Шпаргалка", callback_data="admin:help")],
        ]
    )


def admin_plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Баланс: 99 / 259 / 949", callback_data="admin:plans:set:balance")],
            [InlineKeyboardButton(text="💰 Маржа: 99 / 279 / 1099", callback_data="admin:plans:set:margin")],
            [InlineKeyboardButton(text="🚀 Конверсия: 99 / 239 / 849", callback_data="admin:plans:set:convert")],
            [InlineKeyboardButton(text="⌨️ Показать ручную команду", callback_data="admin:plans:manual")],
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin:home")],
        ]
    )


def payment_methods_keyboard(
    settings: Any,
    *,
    plan_key: str,
    target: str,
    device_id: int = 1,
) -> InlineKeyboardMarkup:
    suffix = f"plan:{plan_key}:all" if target == "all" else f"plan:{plan_key}:slot:{device_id}"
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(
                text=("₿ CryptoBot" if settings.cryptobot_enabled() else "₿ CryptoBot (не настроен)"),
                callback_data=f"buy:crypto:{suffix}",
            )
        ]
    )
    if settings.yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Карта (YooKassa)", callback_data=f"buy:card:{suffix}")])
    if not settings.yookassa_enabled():
        rows.append(
            [InlineKeyboardButton(text="💳 Оплата картой (не настроена)", callback_data=f"buy:card:{suffix}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_target_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    ordered = sorted(devices, key=lambda d: int(d["device_id"]))
    for row in ordered:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔁 Продлить {_short_label(label, limit=22)}",
                    callback_data=f"buyselect:slot:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🧩 Продлить все устройства", callback_data="buyselect:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_plan_keyboard(
    settings: Any,
    *,
    target: str,
    device_id: int = 1,
    devices_count: int = 1,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    multiplier = max(1, devices_count) if target == "all" else 1
    suffix = "all" if target == "all" else f"slot:{device_id}"
    for plan in settings.plans:
        amount = plan.rub * multiplier
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{plan_title(plan)} • {amount:.2f} RUB",
                    callback_data=f"buyplan:{plan.key}:{suffix}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def device_methods_keyboard(settings: Any) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(
                text=("₿ CryptoBot" if settings.cryptobot_enabled() else "₿ CryptoBot (не настроен)"),
                callback_data="device:crypto",
            )
        ]
    )
    if settings.yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Карта (YooKassa)", callback_data="device:card")])
    if not settings.yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Оплата картой (не настроена)", callback_data="device:card")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pay_action_keyboard(provider: str, external_id: str, pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть оплату", url=pay_url)],
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check:{provider}:{external_id}")],
        ]
    )


def renewal_actions_keyboard(*, device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔁 Продлить устройство {device_id}",
                    callback_data=f"buyselect:slot:{device_id}",
                )
            ],
            [InlineKeyboardButton(text="🧩 Продлить все устройства", callback_data="buyselect:all")],
        ]
    )


def broadcast_confirm_keyboard(*, fmt_key: str, with_buttons: bool) -> InlineKeyboardMarkup:
    fmt_label = broadcast_format_label(fmt_key)
    buttons_label = "Кнопки: вкл" if with_buttons else "Кнопки: выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="admin:broadcast_send"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin:broadcast_cancel"),
            ],
            [InlineKeyboardButton(text=f"Формат: {fmt_label}", callback_data="admin:broadcast_fmt")],
            [InlineKeyboardButton(text=buttons_label, callback_data="admin:broadcast_btn")],
        ]
    )


def broadcast_format_label(fmt_key: str) -> str:
    if fmt_key == "markdown":
        return "Markdown"
    if fmt_key == "html":
        return "HTML"
    return "Текст"


def broadcast_parse_mode(fmt_key: str) -> str | None:
    if fmt_key == "markdown":
        return "Markdown"
    if fmt_key == "html":
        return "HTML"
    return None


def broadcast_next_format(fmt_key: str) -> str:
    order = ["plain", "markdown", "html"]
    if fmt_key not in order:
        return "plain"
    idx = (order.index(fmt_key) + 1) % len(order)
    return order[idx]


def devices_rename_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    if not devices:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in devices:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{device_id}. {_short_label(label, limit=22)}",
                    callback_data=f"devrename:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="devrename:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def devices_replace_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    if not devices:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in devices:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{device_id}. {_short_label(label, limit=22)}",
                    callback_data=f"devreplace:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="devreplace:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def device_replace_confirm_keyboard(device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"devreplace_confirm:{device_id}:yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"devreplace_confirm:{device_id}:no",
                ),
            ]
        ]
    )
