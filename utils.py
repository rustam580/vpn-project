from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Mapping
from uuid import uuid4

from src.vpnbot.bot_formatters import format_expire, format_limit, format_time_left, format_used
from config import _absolutize_subscription_link, normalize_config_delivery_mode
from models import MarzbanUser


def build_username(telegram_id: int) -> str:
    return f"tg_{telegram_id}"


def build_device_username(telegram_id: int, device_id: int) -> str:
    if device_id <= 1:
        return build_username(telegram_id)
    return f"tg_{telegram_id}_d{device_id}"


def build_replacement_username(telegram_id: int, device_id: int) -> str:
    suffix = uuid4().hex[:8]
    if device_id <= 1:
        return f"tg_{telegram_id}_r{suffix}"
    return f"tg_{telegram_id}_d{device_id}_r{suffix}"


def extract_start_payload(text: str | None) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def parse_referrer_from_payload(payload: str) -> int | None:
    if not payload.startswith("ref_"):
        return None
    raw = payload[4:].strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _web_bind_signature(order_id: str, *, bot_token: str) -> str:
    digest = hmac.new(
        bot_token.encode("utf-8"),
        order_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:12]


def build_web_bind_payload(order_id: str, *, bot_token: str) -> str:
    normalized = str(order_id or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized):
        return ""
    sig = _web_bind_signature(normalized, bot_token=bot_token)
    return f"webbind_{normalized}_{sig}"


def parse_web_order_from_payload(payload: str, *, bot_token: str) -> str | None:
    raw = str(payload or "").strip()
    if not raw.startswith("webbind_"):
        return None
    body = raw[len("webbind_") :]
    parts = body.split("_", 1)
    if len(parts) != 2:
        return None
    order_id, sig = parts[0].strip().lower(), parts[1].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", order_id):
        return None
    expected = _web_bind_signature(order_id, bot_token=bot_token)
    if not hmac.compare_digest(sig, expected):
        return None
    return order_id


def _as_user(user: Mapping[str, Any] | MarzbanUser) -> MarzbanUser:
    return MarzbanUser.from_payload(user)


def extract_links(user: Mapping[str, Any] | MarzbanUser) -> list[str]:
    marz_user = _as_user(user)
    raw = marz_user.links
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            item = item.strip()
            if item and item not in seen:
                result.append(item)
                seen.add(item)
    return result


def extract_subscription_links(
    user: Mapping[str, Any] | MarzbanUser, *, public_base_url: str = ""
) -> list[str]:
    marz_user = _as_user(user)
    result: list[str] = []
    seen: set[str] = set()

    def _push(value: Any) -> None:
        if not isinstance(value, str):
            return
        item = _absolutize_subscription_link(value, public_base_url)
        if not item:
            return
        if item not in seen:
            seen.add(item)
            result.append(item)

    for candidate in (
        marz_user.subscription_url,
        marz_user.subscription_link,
        marz_user.subscription,
        marz_user.sub_link,
    ):
        _push(candidate)

    extra = marz_user.subscription_links
    if isinstance(extra, list):
        for item in extra:
            _push(item)
    elif isinstance(extra, dict):
        for value in extra.values():
            _push(value)
    return result


def select_delivery_links(
    user: Mapping[str, Any] | MarzbanUser, *, mode: str, public_base_url: str = ""
) -> list[str]:
    direct = extract_links(user)
    subs = extract_subscription_links(user, public_base_url=public_base_url)
    normalized_mode = normalize_config_delivery_mode(mode)
    if normalized_mode == "subscription_only":
        return subs
    if normalized_mode == "subscription_first":
        return subs if subs else direct
    return direct


def status_text(user: Mapping[str, Any] | MarzbanUser) -> str:
    marz_user = _as_user(user)
    links = extract_links(marz_user)
    expire_ts = marz_user.expire
    status = marz_user.status
    status_icon = "🟢" if status == "active" else "⚪"
    status_label = {
        "active": "активен",
        "disabled": "отключен",
        "expired": "истек",
        "limited": "ограничен",
    }.get(status, status)
    cfg_count = 1 if links else 0
    return (
        f"👤 <b>Профиль:</b> {marz_user.username or 'unknown'}\n"
        f"{status_icon} <b>Статус:</b> {status_label}\n"
        f"📊 <b>Трафик:</b> {format_used(marz_user.used_traffic)} из {format_limit(marz_user.data_limit)}\n"
        f"🗓 <b>Действует до:</b> {format_expire(expire_ts)}\n"
        f"⏳ <b>Осталось:</b> {format_time_left(expire_ts)}\n"
        f"🔗 <b>Ссылок:</b> {cfg_count}"
    )
