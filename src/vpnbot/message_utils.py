"""Pure helpers for message construction and chunking.

`split_message` slices long text into chunks under a Telegram-friendly limit,
preserving line boundaries where possible. The thin wrappers around
`app_texts` are kept here so handlers and tests have a stable, package-local
import surface.
"""

from __future__ import annotations

from app_texts import (
    build_config_import_hint_text,
    build_quick_connect_guide_text,
)


def split_message(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit and current:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def quick_connect_guide_text() -> str:
    return build_quick_connect_guide_text()


def config_import_hint_text() -> str:
    return build_config_import_hint_text()
