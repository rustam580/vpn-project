"""Authorization predicates for the bot runtime."""

from __future__ import annotations

from config import Settings


def is_admin(telegram_id: int | None, settings: Settings) -> bool:
    return telegram_id is not None and telegram_id in settings.admin_ids
