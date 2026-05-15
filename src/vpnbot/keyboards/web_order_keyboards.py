"""Inline keyboards for support-oriented web order cards.

The callbacks here are intentionally read-only. Mutating recovery actions
remain in the drift resolver flow, where every action is re-audited first.
"""
from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ACTION_CHECK_PAYMENT = "c"

CALLBACK_PREFIX = "wo:"
MAX_CALLBACK_BYTES = 64


def encode_web_order_callback(action: str, order_id: str) -> str:
    return f"{CALLBACK_PREFIX}{action}:{str(order_id).strip()}"


def parse_web_order_callback(data: str) -> tuple[str, str] | None:
    """Return (action, order_id) for valid `wo:*` callback_data, else None."""
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    tail = data[len(CALLBACK_PREFIX):]
    parts = tail.split(":", 1)
    if len(parts) != 2:
        return None
    action, order_id = parts[0].strip(), parts[1].strip()
    if action != ACTION_CHECK_PAYMENT or not order_id:
        return None
    return action, order_id


def web_order_support_keyboard(
    order: dict[str, Any],
    *,
    linked_tg_ids: set[int] | None = None,
) -> InlineKeyboardMarkup | None:
    """Build safe support buttons for a web order card."""
    order_id = str(order.get("order_id") or "").strip()
    rows: list[list[InlineKeyboardButton]] = []

    if order_id and str(order.get("provider") or "").strip() and str(order.get("external_id") or "").strip():
        data = encode_web_order_callback(ACTION_CHECK_PAYMENT, order_id)
        if len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES:
            rows.append([InlineKeyboardButton(text="Check payment status", callback_data=data)])

    tg_ids = sorted(int(tg_id) for tg_id in (linked_tg_ids or set()) if int(tg_id) > 0)
    for tg_id in tg_ids[:3]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Open customer {tg_id}",
                    switch_inline_query_current_chat=f"/user {tg_id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="Run drift audit", callback_data="admin:sync_audit")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
