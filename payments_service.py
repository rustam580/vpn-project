import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import uuid4

import httpx


def _plan_gb_for_desc(gb: int) -> str:
    return "UNLIM" if gb <= 0 else f"{gb}GB"


async def cryptobot_create_invoice(settings: Any, telegram_id: int) -> tuple[str, str]:
    base = "https://testnet-pay.crypt.bot" if settings.cryptobot_testnet else "https://pay.crypt.bot"
    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client:
        payload: dict[str, Any] = {
            "currency_type": "fiat",
            "fiat": settings.cryptobot_fiat,
            "amount": f"{settings.pay_rub:.2f}",
            "description": f"VPN {settings.pay_days}d {_plan_gb_for_desc(settings.pay_gb)}",
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


async def yookassa_create_payment(settings: Any, telegram_id: int) -> tuple[str, str]:
    payload = {
        "amount": {"value": f"{settings.pay_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "description": f"VPN {settings.pay_days}d {_plan_gb_for_desc(settings.pay_gb)}",
        "metadata": {"telegram_id": str(telegram_id)},
        "confirmation": {"type": "redirect", "return_url": settings.yookassa_return_url},
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


def _altyn_unwrap_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], dict):
            return data["data"]
        return data
    return {}


def _altyn_sign_headers(
    *,
    settings: Any,
    absolute_url: str,
    body_text: str,
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    string_to_sign = f"{timestamp}\n{nonce}\n{absolute_url}\n{body_text}"
    signature_bytes = hmac.new(
        settings.altyn_api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = "v1=" + base64.b64encode(signature_bytes).decode("ascii")
    return {
        "X-API-Key-Id": settings.altyn_api_key_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


async def altyn_create_payment(settings: Any, telegram_id: int) -> tuple[str, str]:
    external_id = f"tg{telegram_id}_{int(time.time())}"
    payload = {
        "account_number": settings.altyn_account_number,
        "bank_id": settings.altyn_bank_id,
        "amount": f"{settings.pay_rub:.2f}",
        "currency": "RUB",
        "external_id": external_id,
    }
    body_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    url = f"{settings.altyn_base_url}/payment/sbp/"
    headers = _altyn_sign_headers(settings=settings, absolute_url=url, body_text=body_text)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, content=body_text.encode("utf-8"), headers=headers)
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"Altyn create payment failed: {r.status_code} {data}")
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"Altyn create payment failed: {data}")
    obj = _altyn_unwrap_payload(data)
    token = str(obj.get("id", "")).strip()
    pay_url = str(obj.get("qr_url") or obj.get("deep_link") or "").strip()
    if not token:
        raise RuntimeError(f"Altyn bad create response: {data}")
    if not pay_url:
        pay_url = f"{settings.altyn_base_url}/payment/{token}/"
    return token, pay_url


async def altyn_check_payment(settings: Any, external_id: str) -> str:
    url = f"{settings.altyn_base_url}/payment/{external_id}/"
    headers = _altyn_sign_headers(settings=settings, absolute_url=url, body_text="")
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers=headers)
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"Altyn get payment failed: {r.status_code} {data}")
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"Altyn get payment failed: {data}")
    obj = _altyn_unwrap_payload(data)
    status_value = obj.get("status")
    if status_value is None:
        return "unknown"
    return str(status_value)
