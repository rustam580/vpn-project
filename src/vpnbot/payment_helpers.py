"""Payment provider wrappers and orchestration glue.

Two layers live here:
- thin `cryptobot_*` / `yookassa_*` wrappers around `payments_service` so
  callers (and tests via monkey-patching) target a stable package-local
  symbol;
- `apply_paid_payment` and `apply_referral_bonus_if_needed`, which inject
  the bot-runtime side effects (device provisioning, access extension,
  admin notifications) into the otherwise-pure `payment_flow` service.

`check_and_apply_payment` deliberately stays in `bot_runtime` so that
existing tests keep working: they monkey-patch
`bot.cryptobot_check_invoice` / `bot.apply_paid_payment` and rely on the
in-module name lookup happening at call time.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot

from config import Settings
from src.vpnbot.bot_access import (
    apply_referral_bonus_if_needed as _apply_referral_bonus_if_needed,
    ensure_device,
    extend_access_all_devices,
    extend_access_device,
)
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.messaging import notify_access_updated
from src.vpnbot.notifications import notify_admin_payment
from src.vpnbot.services.bot_marzban import MarzbanClient
from src.vpnbot.services.payment_flow import (
    apply_paid_payment as _pf_apply_paid_payment,
)
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


async def apply_referral_bonus_if_needed(
    *,
    paid_telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    bot: Bot | None = None,
) -> None:
    await _apply_referral_bonus_if_needed(
        paid_telegram_id=paid_telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        bot=bot,
        notify_access_updated_fn=notify_access_updated,
    )


async def apply_paid_payment(
    *,
    provider: str,
    external_id: str,
    payment: dict[str, Any],
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    bot: Bot | None,
    strict_device_slot: bool,
) -> tuple[dict[str, Any], str, str | None]:
    return await _pf_apply_paid_payment(
        provider=provider,
        external_id=external_id,
        payment=payment,
        repo=repo,
        marzban=marzban,
        settings=settings,
        bot=bot,
        strict_device_slot=strict_device_slot,
        ensure_device_fn=ensure_device,
        extend_access_device_fn=extend_access_device,
        extend_access_all_devices_fn=extend_access_all_devices,
        apply_referral_bonus_if_needed_fn=apply_referral_bonus_if_needed,
        notify_admin_payment_fn=notify_admin_payment,
    )
