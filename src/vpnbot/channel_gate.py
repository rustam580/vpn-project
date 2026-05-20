from __future__ import annotations

import re
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.vpnbot.env_utils import normalize_channel_url

CHANNEL_GATE_ALLOWED_COMMANDS = {"start", "help", "support", "channel"}
CHANNEL_GATE_ALLOWED_CALLBACK_PREFIXES = ("quick:channel",)


def channel_gate_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "channel_subscription_required", False))


def normalize_channel_chat_id(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith("@"):
        slug = value.lstrip("@").strip("/")
        return f"@{slug}" if slug else ""
    if re.fullmatch(r"-?\d{5,32}", value):
        return value

    lower = value.lower()
    if lower.startswith("https://t.me/") or lower.startswith("http://t.me/"):
        tail = value.split("t.me/", 1)[1].strip("/")
    elif lower.startswith("t.me/"):
        tail = value.split("t.me/", 1)[1].strip("/")
    else:
        tail = value.strip("/")

    if not tail or tail.startswith("+") or tail.startswith("joinchat/"):
        return ""
    if re.fullmatch(r"[A-Za-z0-9_]{4,64}", tail):
        return f"@{tail}"
    return ""


def channel_gate_chat_id(settings: Any) -> str:
    explicit = str(getattr(settings, "channel_chat_id", "") or "").strip()
    if explicit:
        return normalize_channel_chat_id(explicit)
    return normalize_channel_chat_id(str(getattr(settings, "channel_url", "") or ""))


def channel_gate_url(settings: Any) -> str:
    link = normalize_channel_url(str(getattr(settings, "channel_url", "") or ""))
    if link:
        return link
    chat_id = str(getattr(settings, "channel_chat_id", "") or "").strip()
    if chat_id.startswith("@"):
        return normalize_channel_url(chat_id) or ""
    return ""


def is_channel_member_status(status: Any) -> bool:
    value = getattr(status, "value", status)
    text = str(value or "").lower()
    return text in {"creator", "administrator", "member"} or text.endswith(
        (".creator", ".administrator", ".member")
    )


def channel_gate_allowed_message(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return False
    command = raw.split(maxsplit=1)[0].split("@", 1)[0].lstrip("/").lower()
    return command in CHANNEL_GATE_ALLOWED_COMMANDS


def channel_gate_allowed_callback(data: str | None) -> bool:
    raw = str(data or "")
    return any(raw.startswith(prefix) for prefix in CHANNEL_GATE_ALLOWED_CALLBACK_PREFIXES)


def channel_required_text(settings: Any) -> str:
    link = channel_gate_url(settings)
    suffix = f"\n\nКанал: {link}" if link else ""
    return (
        "Чтобы пользоваться ботом, подпишитесь на наш канал.\n"
        "После подписки вернитесь в бот и нажмите нужную кнопку еще раз."
        f"{suffix}"
    )


def channel_required_keyboard(settings: Any) -> InlineKeyboardMarkup | None:
    link = channel_gate_url(settings)
    if not link:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на канал", url=link)],
        ]
    )
