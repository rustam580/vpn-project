"""Pure helpers for editing the on-disk `.env` file and normalising user-facing URL/value inputs.

These utilities are deliberately decoupled from aiogram/Marzban code so they can be unit-tested
in isolation and reused by handlers without pulling in the full bot runtime.
"""

from __future__ import annotations

import re
from pathlib import Path


def update_env_file(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if path.exists():
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        for line in raw_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                lines.append(line)
                continue
            k, _ = line.split("=", 1)
            if k.strip() == key:
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def coerce_env_value(value: str, kind: str) -> str | None:
    raw = value.strip()
    if kind == "int":
        try:
            return str(int(raw))
        except ValueError:
            return None
    if kind == "float":
        try:
            return f"{float(raw.replace(',', '.')):.2f}"
        except ValueError:
            return None
    if kind == "bool":
        normalized = raw.lower()
        if normalized in {"1", "true", "yes", "on"}:
            return "1"
        if normalized in {"0", "false", "no", "off"}:
            return "0"
        return None
    return raw


def normalize_channel_url(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if value.startswith("@"):
        slug = value.lstrip("@").strip("/")
        return f"https://t.me/{slug}" if slug else None
    lower = value.lower()
    if lower.startswith("https://t.me/") or lower.startswith("http://t.me/"):
        return value
    if lower.startswith("t.me/"):
        return f"https://{value}"
    if re.fullmatch(r"[A-Za-z0-9_]{4,64}", value):
        return f"https://t.me/{value}"
    if lower.startswith("https://") or lower.startswith("http://"):
        return value
    return None
