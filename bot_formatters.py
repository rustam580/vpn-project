from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

BYTES_IN_GB = 1024**3


# Keep titles consistent with existing tariff copy in bot.py.
def _default_plan_title(days: int) -> str:
    if days == 30:
        return "1 месяц"
    if days == 90:
        return "3 месяца"
    if days == 365:
        return "12 месяцев"
    return f"{days} дней"


def format_used(v: int) -> str:
    return f"{max(v, 0) / BYTES_IN_GB:.1f} GB"


def format_limit(v: int) -> str:
    return "Без лимита" if v <= 0 else f"{v / BYTES_IN_GB:.1f} GB"


def plan_gb_text(gb: int) -> str:
    return "Безлимит" if gb <= 0 else f"{gb} GB"


def plan_gb_for_desc(gb: int) -> str:
    return "UNLIM" if gb <= 0 else f"{gb}GB"


def plan_title(plan: Any) -> str:
    title = str(getattr(plan, "title", "")).strip()
    if title:
        return title
    days = int(getattr(plan, "days", 30) or 30)
    return _default_plan_title(days)


def plan_offer_text(plan: Any, *, multiplier: int = 1) -> str:
    amount = max(1, multiplier) * float(getattr(plan, "rub", 0) or 0)
    days = int(getattr(plan, "days", 0) or 0)
    gb = int(getattr(plan, "gb", 0) or 0)
    return (
        f"{plan_title(plan)} — {amount:.2f} RUB\n"
        f"Срок: +{days} дней, трафик: {plan_gb_text(gb)}"
    )


def plans_list_text(settings: Any, *, multiplier: int = 1) -> str:
    lines = []
    for plan in settings.plans:
        lines.append(f"- {plan_offer_text(plan, multiplier=multiplier)}")
    return "\n".join(lines)


def admin_plans_text(settings: Any) -> str:
    lines = ["Текущие тарифы:"]
    for plan in settings.plans:
        lines.append(
            f"- {plan.key}: {plan_title(plan)} • {plan.rub:.2f} RUB • {plan.days} дн • {plan_gb_text(plan.gb)}"
        )
    return "\n".join(lines)


def format_expire(v: int) -> str:
    if v <= 0:
        return "Без срока"
    return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def format_time_left(expire_ts: int) -> str:
    if expire_ts <= 0:
        return "Без ограничения по сроку"
    now = int(time.time())
    delta = expire_ts - now
    if delta <= 0:
        return "Срок истек"
    days = delta // 86400
    hours = (delta % 86400) // 3600
    if days > 0:
        return f"{days} дн. {hours} ч."
    return f"{hours} ч."


def format_last_online(raw: Any) -> str:
    if raw is None:
        return "нет данных"
    text = str(raw).strip()
    if not text:
        return "нет данных"
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except Exception:
        return text
