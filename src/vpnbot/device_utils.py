"""Pure helpers for device labels, names, and slot allocation.

Used by the bot runtime, keyboards, and fallback handler. No aiogram or
storage dependencies — safe to import anywhere.
"""

from __future__ import annotations


def _device_label(device_id: int, device_name: str | None) -> str:
    name = (device_name or "").strip()
    if name:
        return name
    return f"Устройство {device_id}"


def _short_label(label: str, limit: int = 18) -> str:
    if len(label) <= limit:
        return label
    return f"{label[:limit - 1]}…"


def normalize_device_name(raw: str, limit: int = 32) -> str | None:
    name = " ".join(raw.strip().split())
    if not name:
        return None
    if len(name) > limit:
        return name[:limit]
    return name


def format_device_limit(limit: int) -> str:
    if limit <= 0:
        return "без ограничений"
    return str(limit)


def next_device_slot(used_slots: set[int], limit: int) -> int | None:
    if limit > 0:
        for candidate in range(2, limit + 1):
            if candidate not in used_slots:
                return candidate
        return None
    candidate = 2
    while candidate in used_slots:
        candidate += 1
    return candidate
