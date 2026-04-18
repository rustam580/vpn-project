import time
from typing import Any
from uuid import uuid4

import httpx


def _plan_gb_for_desc(gb: int) -> str:
    return "UNLIM" if gb <= 0 else f"{gb}GB"


async def cryptobot_create_invoice(
    settings: Any,
    telegram_id: int,
    *,
    amount_rub: float | None = None,
    description: str | None = None,
) -> tuple[str, str]:
    base = "https://testnet-pay.crypt.bot" if settings.cryptobot_testnet else "https://pay.crypt.bot"
    amount = settings.pay_rub if amount_rub is None else float(amount_rub)
    desc = description or f"VPN {settings.pay_days}d {_plan_gb_for_desc(settings.pay_gb)}"
    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client:
        payload: dict[str, Any] = {
            "currency_type": "fiat",
            "fiat": settings.cryptobot_fiat,
            "amount": f"{amount:.2f}",
            "description": desc,
            "payload": f"tg:{telegram_id}:{int(time.time())}",
            "expires_in": settings.cryptobot_expires_in,
        }
        if settings.cryptobot_accepted_assets:
            payload["accepted_assets"] = settings.cryptobot_accepted_assets
        r = await client.post(
            "/api/createInvoice",
            json=payload,
            headers={"Crypto-Pay-API-Token": settings.cryptobot_token},
        )
        body = r.json()
        if r.status_code >= 400 or not body.get("ok"):
            raise RuntimeError(f"CryptoBot createInvoice failed: {r.status_code} {body}")
        data = body.get("result", {})
        external_id = str(data.get("invoice_id", "")).strip()
        pay_url = str(
            data.get("bot_invoice_url")
            or data.get("mini_app_invoice_url")
            or data.get("web_app_invoice_url")
            or ""
        ).strip()
        if not external_id or not pay_url:
            raise RuntimeError(f"CryptoBot createInvoice bad response: {data}")
        return external_id, pay_url


async def cryptobot_check_invoice(settings: Any, external_id: str) -> str:
    base = "https://testnet-pay.crypt.bot" if settings.cryptobot_testnet else "https://pay.crypt.bot"
    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client:
        r = await client.get(
            "/api/getInvoices",
            params={"invoice_ids": external_id},
            headers={"Crypto-Pay-API-Token": settings.cryptobot_token},
        )
        body = r.json()
        if r.status_code >= 400 or not body.get("ok"):
            raise RuntimeError(f"CryptoBot getInvoices failed: {r.status_code} {body}")
        items = body.get("result", {}).get("items", [])
        for item in items:
            if str(item.get("invoice_id")) == external_id:
                return str(item.get("status", "pending"))
        return "pending"


async def yookassa_create_payment(
    settings: Any,
    telegram_id: int,
    *,
    amount_rub: float | None = None,
    description: str | None = None,
    return_url: str | None = None,
) -> tuple[str, str]:
    amount = settings.pay_rub if amount_rub is None else float(amount_rub)
    desc = description or f"VPN {settings.pay_days}d {_plan_gb_for_desc(settings.pay_gb)}"
    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "capture": True,
        "description": desc,
        "metadata": {"telegram_id": str(telegram_id)},
        "confirmation": {
            "type": "redirect",
            "return_url": (return_url or settings.yookassa_return_url),
        },
    }
    async with httpx.AsyncClient(base_url="https://api.yookassa.ru", timeout=20.0) as client:
        r = await client.post(
            "/v3/payments",
            json=payload,
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            headers={"Idempotence-Key": str(uuid4())},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"YooKassa create payment failed: {r.status_code} {r.text}")
        data = r.json()
        external_id = str(data.get("id", "")).strip()
        pay_url = str((data.get("confirmation") or {}).get("confirmation_url", "")).strip()
        if not external_id or not pay_url:
            raise RuntimeError(f"YooKassa bad create response: {data}")
        return external_id, pay_url


async def yookassa_check_payment(settings: Any, external_id: str) -> str:
    async with httpx.AsyncClient(base_url="https://api.yookassa.ru", timeout=20.0) as client:
        r = await client.get(
            f"/v3/payments/{external_id}",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        )
        if r.status_code >= 400:
            raise RuntimeError(f"YooKassa get payment failed: {r.status_code} {r.text}")
        data = r.json()
        return str(data.get("status", "pending"))
