from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any, Protocol


class RepoLike(Protocol):
    async def get_user_by_username(self, marzban_username: str) -> dict[str, Any] | None: ...

    async def get_device_by_username(self, marzban_username: str) -> dict[str, Any] | None: ...


class MarzbanLike(Protocol):
    async def get_user(self, username: str) -> dict[str, Any] | None: ...


async def build_web_order_profile_lines(
    order: dict[str, Any],
    *,
    repo: RepoLike,
    marzban: MarzbanLike,
) -> tuple[list[str], set[int]]:
    """Render a support-oriented web order card and return linked Telegram IDs."""
    linked_tg_ids: set[int] = set()
    order_id = str(order.get("order_id") or "")
    provider = str(order.get("provider") or "")
    external_id = str(order.get("external_id") or "")
    status = str(order.get("status") or "")
    plan = str(order.get("plan_key") or "")
    username = str(order.get("marzban_username") or "").strip()
    contact = str(order.get("customer_contact") or "").strip()
    amount = float(order.get("amount_rub") or 0)
    days = int(order.get("days") or 0)
    gb = int(order.get("gb") or 0)

    lines = [
        f"🌐 Web order: <code>{html.escape(order_id)}</code>",
        f"- payment: {html.escape(provider)}:<code>{html.escape(external_id)}</code>",
        f"- status: {html.escape(status)}",
        f"- plan: {html.escape(plan)}, {days}d, {_gb_text(gb)}, {amount:.2f} RUB",
        f"- updated: {_format_ts(order.get('updated_at'))}",
    ]
    if contact:
        lines.append(f"- contact: {html.escape(contact)}")

    if username:
        lines.append(f"- Marzban username: <code>{html.escape(username)}</code>")
        user_row = await repo.get_user_by_username(username)
        device_row = await repo.get_device_by_username(username)
        if user_row:
            linked_tg_ids.add(int(user_row["telegram_id"]))
            lines.append(f"- DB user: TG <code>{int(user_row['telegram_id'])}</code>")
        if device_row:
            linked_tg_ids.add(int(device_row["telegram_id"]))
            lines.append(
                f"- DB device: TG <code>{int(device_row['telegram_id'])}</code>, "
                f"slot <code>{int(device_row['device_id'])}</code>"
            )
        mz_user = await marzban.get_user(username)
        if mz_user:
            lines.append(
                f"- Marzban: {html.escape(str(mz_user.get('status', 'unknown')))}, "
                f"expire={_format_expire(mz_user.get('expire'))}"
            )
        else:
            lines.append("- Marzban: missing")
    else:
        lines.append("- Marzban username: empty")

    lines.extend(_action_lines(provider=provider, external_id=external_id, order_id=order_id, linked_tg_ids=linked_tg_ids))
    return lines, linked_tg_ids


def _action_lines(
    *,
    provider: str,
    external_id: str,
    order_id: str,
    linked_tg_ids: set[int],
) -> list[str]:
    lines = ["- support actions:"]
    if linked_tg_ids:
        for tg_id in sorted(linked_tg_ids):
            lines.append(f"  - full customer card: <code>/user {tg_id}</code>")
    else:
        lines.append("  - no Telegram link yet: ask client to bind Telegram from the website/order page")
    if provider and external_id:
        lines.append(f"  - recheck payment: <code>/check {html.escape(provider)} {html.escape(external_id)}</code>")
    if order_id:
        lines.append(f"  - find this order again: <code>/user {html.escape(order_id)}</code>")
    lines.append("  - access drift: use <code>/sync_audit</code> or the Marzban/DB audit button")
    return lines


def _gb_text(gb: int) -> str:
    return "unlimited" if gb <= 0 else f"{gb} GB"


def _format_ts(raw: Any) -> str:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "n/a"
    return datetime.fromtimestamp(value, tz=UTC).strftime("%d.%m.%Y %H:%M UTC")


def _format_expire(raw: Any) -> str:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "no_expire"
    return _format_ts(value)
