from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


class RepoLike(Protocol):
    async def get_user(self, telegram_id: int) -> dict[str, Any] | None: ...

    async def list_devices(self, telegram_id: int) -> list[dict[str, Any]]: ...

    async def list_payments_for_user(self, telegram_id: int, *, limit: int = 10) -> list[dict[str, Any]]: ...

    async def list_web_orders_for_usernames(
        self, usernames: list[str], *, limit: int = 10
    ) -> list[dict[str, Any]]: ...


class MarzbanLike(Protocol):
    async def get_user(self, username: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class CustomerProfileFormatters:
    build_username: Any
    format_expire: Any
    format_limit: Any
    format_used: Any
    format_last_online: Any
    device_label: Any


@dataclass(frozen=True)
class ChatIdentity:
    first_name: str = ""
    last_name: str = ""
    username: str = ""

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()


async def build_customer_profile_text(
    *,
    telegram_id: int,
    repo: RepoLike,
    marzban: MarzbanLike,
    fmt: CustomerProfileFormatters,
    chat: ChatIdentity | None = None,
    payment_limit: int = 5,
    web_order_limit: int = 5,
) -> str:
    warnings: list[str] = []
    lines: list[str] = [f"👤 Клиент: <a href=\"tg://user?id={telegram_id}\">ID {telegram_id}</a>"]

    if chat is not None:
        if chat.display_name:
            lines.append(f"Имя: {html.escape(chat.display_name)}")
        if chat.username:
            lines.append(f"Username: @{html.escape(chat.username)}")

    row = await repo.get_user(telegram_id)
    primary_username = str(row["marzban_username"]) if row else fmt.build_username(telegram_id)
    primary_user = await marzban.get_user(primary_username)
    if row:
        lines.append(_marzban_line("Primary", primary_username, primary_user, warnings))
    elif primary_user:
        lines.append(_marzban_line("Primary guessed", primary_username, primary_user, warnings))
    else:
        lines.append("Primary: не найден в DB/Marzban")
        warnings.append("нет primary-профиля")

    if primary_user:
        lines.extend(_marzban_state_lines(primary_user, fmt))

    devices = await repo.list_devices(telegram_id)
    usernames = [primary_username]
    if devices:
        lines.append("")
        lines.append(f"📱 Устройства: {len(devices)}")
        for device in devices:
            device_id = int(device["device_id"])
            username = str(device.get("marzban_username") or "").strip()
            label = fmt.device_label(device_id, device.get("device_name"))
            if username:
                usernames.append(username)
            mz_user = await marzban.get_user(username) if username else None
            if not mz_user:
                warnings.append(f"устройство {device_id}: {username or 'empty'} не найдено в Marzban")
                state = "missing_in_marzban"
            else:
                state = _compact_marzban_state(mz_user, fmt)
            lines.append(
                f"- {device_id}. {html.escape(str(label))}: "
                f"<code>{html.escape(username or '-')}</code> — {html.escape(state)}"
            )
    else:
        lines.append("")
        lines.append("📱 Устройства: нет в DB")

    payments = await repo.list_payments_for_user(telegram_id, limit=payment_limit)
    lines.append("")
    lines.append(f"💳 Платежи: {len(payments)}")
    if payments:
        for payment in payments:
            lines.append(_payment_line(payment))
    else:
        lines.append("- нет данных")

    web_orders = await repo.list_web_orders_for_usernames(usernames, limit=web_order_limit)
    lines.append("")
    lines.append(f"🌐 Web-заказы: {len(web_orders)}")
    if web_orders:
        for order in web_orders:
            lines.append(_web_order_line(order))
    else:
        lines.append("- нет привязанных заказов")

    if warnings:
        lines.append("")
        lines.append("⚠️ Внимание:")
        for warning in warnings[:8]:
            lines.append(f"- {html.escape(warning)}")
        if len(warnings) > 8:
            lines.append(f"- еще {len(warnings) - 8}")

    return "\n".join(lines)


def _marzban_line(
    label: str,
    username: str,
    user: dict[str, Any] | None,
    warnings: list[str],
) -> str:
    if user:
        return f"{label}: <code>{html.escape(username)}</code>"
    warnings.append(f"{label.lower()} {username} не найден в Marzban")
    return f"{label}: <code>{html.escape(username)}</code> (не найден в Marzban)"


def _marzban_state_lines(user: dict[str, Any], fmt: CustomerProfileFormatters) -> list[str]:
    expire_ts = int(user.get("expire", 0) or 0)
    data_limit = int(user.get("data_limit", 0) or 0)
    used = int(user.get("used_traffic", 0) or 0)
    status = str(user.get("status", "unknown"))
    return [
        f"Статус: {html.escape(status)}",
        f"Действует до: {fmt.format_expire(expire_ts)}",
        f"Трафик: {fmt.format_used(used)} из {fmt.format_limit(data_limit)}",
    ]


def _compact_marzban_state(user: dict[str, Any], fmt: CustomerProfileFormatters) -> str:
    status = str(user.get("status", "unknown"))
    expire_ts = int(user.get("expire", 0) or 0)
    used = int(user.get("used_traffic", 0) or 0)
    online = fmt.format_last_online(user.get("online_at") or user.get("last_online") or user.get("last_online_at"))
    return f"{status}, до {fmt.format_expire(expire_ts)}, used {fmt.format_used(used)}, online {online}"


def _payment_line(row: dict[str, Any]) -> str:
    provider = html.escape(str(row.get("provider") or ""))
    external_id = html.escape(str(row.get("external_id") or ""))
    purpose = html.escape(str(row.get("purpose") or ""))
    status = html.escape(str(row.get("status") or ""))
    amount = float(row.get("amount_rub") or 0)
    updated = _format_ts(row.get("updated_at"))
    slot = row.get("device_slot") or "-"
    return f"- {provider}:<code>{external_id}</code>, {purpose}, slot={slot}, {amount:.2f} RUB, {status}, {updated}"


def _web_order_line(row: dict[str, Any]) -> str:
    order_id = html.escape(str(row.get("order_id") or ""))
    provider = html.escape(str(row.get("provider") or ""))
    status = html.escape(str(row.get("status") or ""))
    plan = html.escape(str(row.get("plan_key") or ""))
    amount = float(row.get("amount_rub") or 0)
    username = html.escape(str(row.get("marzban_username") or "-"))
    updated = _format_ts(row.get("updated_at"))
    return f"- <code>{order_id}</code>, {provider}, {status}, {plan}, {amount:.2f} RUB, user=<code>{username}</code>, {updated}"


def _format_ts(raw: Any) -> str:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "n/a"
    return datetime.fromtimestamp(value, tz=UTC).strftime("%d.%m.%Y %H:%M UTC")
