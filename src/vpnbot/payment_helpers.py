"""Thin signature-preserving wrappers around `payments_service`.

These exist so that:
- the bot runtime/tests can monkey-patch a single, package-local symbol
  (e.g. `bot.cryptobot_check_invoice`) without touching the underlying
  service module;
- callers don't need to import the verbose `ps_*` aliases used inside
  `bot_runtime` to disambiguate.
"""

from __future__ import annotations

from config import Settings
from src.vpnbot.services.payments_service import (
    cryptobot_check_invoice as _ps_cryptobot_check_invoice,
    cryptobot_create_invoice as _ps_cryptobot_create_invoice,
    yookassa_check_payment as _ps_yookassa_check_payment,
    yookassa_create_payment as _ps_yookassa_create_payment,
)


async def cryptobot_create_invoice(
    settings: Settings,
    telegram_id: int,
    *,
    amount_rub: float | None = None,
    description: str | None = None,
) -> tuple[str, str]:
    return await _ps_cryptobot_create_invoice(
        settings,
        telegram_id,
        amount_rub=amount_rub,
        description=description,
    )


async def cryptobot_check_invoice(settings: Settings, external_id: str) -> str:
    return await _ps_cryptobot_check_invoice(settings, external_id)


async def yookassa_create_payment(
    settings: Settings,
    telegram_id: int,
    *,
    amount_rub: float | None = None,
    description: str | None = None,
    return_url: str | None = None,
) -> tuple[str, str]:
    return await _ps_yookassa_create_payment(
        settings,
        telegram_id,
        amount_rub=amount_rub,
        description=description,
        return_url=return_url,
    )


async def yookassa_check_payment(settings: Settings, external_id: str) -> str:
    return await _ps_yookassa_check_payment(settings, external_id)
