"""Inline keyboards and callback parsing for drift-resolution actions.

callback_data format: `da:<finding_id>:<action_letter>`.

`action_letter` is single-char (kept short to fit the 64-byte Telegram limit):
    r = recreate Marzban user (only for missing_in_marzban + tg_* username)
    d = drop DB ref (only for missing_in_marzban)
    w = retry web order create+attach (only for web_order_no_access)
    i = ignore (any kind)
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.vpnbot.marzban_sync import (
    KIND_MISSING_IN_MARZBAN,
    KIND_WEB_ORDER_NO_ACCESS,
    DriftFinding,
)

ACTION_RECREATE = "r"
ACTION_DROP_DB_REF = "d"
ACTION_RETRY_WEB_ORDER = "w"
ACTION_IGNORE = "i"

_ACTIONS = frozenset({ACTION_RECREATE, ACTION_DROP_DB_REF, ACTION_RETRY_WEB_ORDER, ACTION_IGNORE})

CALLBACK_PREFIX = "da:"


def encode_drift_callback(finding_id: str, action: str) -> str:
    return f"{CALLBACK_PREFIX}{finding_id}:{action}"


def parse_drift_callback(data: str) -> tuple[str, str] | None:
    """Return (finding_id, action) for valid `da:*` callback_data, else None."""
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    tail = data[len(CALLBACK_PREFIX):]
    # finding_id is itself "kind_prefix:key", so we expect AT LEAST two ':' in tail.
    parts = tail.rsplit(":", 1)
    if len(parts) != 2:
        return None
    finding_id, action = parts
    if action not in _ACTIONS or not finding_id:
        return None
    return finding_id, action


def drift_finding_keyboard(finding: DriftFinding) -> InlineKeyboardMarkup:
    """Build an inline keyboard offering only the actions safe for this finding."""
    buttons: list[list[InlineKeyboardButton]] = []
    if finding.kind == KIND_MISSING_IN_MARZBAN:
        username = str(finding.payload.get("username") or "")
        row: list[InlineKeyboardButton] = []
        if username.startswith("tg_"):
            row.append(
                InlineKeyboardButton(
                    text="♻ Recreate",
                    callback_data=encode_drift_callback(finding.finding_id, ACTION_RECREATE),
                )
            )
        row.append(
            InlineKeyboardButton(
                text="🗑 Drop DB ref",
                callback_data=encode_drift_callback(finding.finding_id, ACTION_DROP_DB_REF),
            )
        )
        buttons.append(row)
    elif finding.kind == KIND_WEB_ORDER_NO_ACCESS:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="♻ Retry create",
                    callback_data=encode_drift_callback(finding.finding_id, ACTION_RETRY_WEB_ORDER),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🙈 Ignore",
                callback_data=encode_drift_callback(finding.finding_id, ACTION_IGNORE),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
