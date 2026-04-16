from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from aiohttp import web

import bot
from bot_marzban import MarzbanClient
from bot_repo import Repo
from payments_service import (
    cryptobot_check_invoice,
    cryptobot_create_invoice,
    yookassa_check_payment,
    yookassa_create_payment,
)


@dataclass(frozen=True)
class WebsiteRuntime:
    host: str
    port: int
    public_url: str
    support_url: str
    enable_crypto: bool
    bot_username: str

    @staticmethod
    def load(settings: bot.Settings) -> "WebsiteRuntime":
        host = str(os.getenv("WEBSITE_API_HOST", "127.0.0.1")).strip()
        port = int(os.getenv("WEBSITE_API_PORT", "8011"))
        public_url = str(os.getenv("WEBSITE_PUBLIC_URL", "http://rootvpn.tech")).strip().rstrip("/")
        support_url = str(os.getenv("WEBSITE_SUPPORT_URL", "")).strip()
        if not support_url and settings.support_username:
            support_url = f"https://t.me/{settings.support_username}"
        enable_crypto = (
            str(os.getenv("WEBSITE_ENABLE_CRYPTO", "true")).strip().lower() in {"1", "true", "yes", "on"}
        )
        bot_username = str(os.getenv("WEBSITE_BOT_USERNAME", "")).strip().lstrip("@")
        return WebsiteRuntime(
            host=host,
            port=port,
            public_url=public_url,
            support_url=support_url,
            enable_crypto=enable_crypto,
            bot_username=bot_username,
        )


def _json_error(message: str, *, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def _normalize_contact(raw: Any) -> str:
    value = str(raw or "").strip()
    return value[:160]


def _normalize_subscription_url(raw: Any) -> str:
    value = str(raw or "").strip()
    return value[:1200]


def _extract_subscription_token(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    is_url = value.startswith(("http://", "https://"))
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        path = parsed.path or ""
    else:
        path = value
    marker = "/sub/"
    if marker in path:
        token = path.split(marker, 1)[1].strip()
    else:
        if is_url:
            return ""
        token = path.strip("/")
    if "/" in token:
        token = token.split("/", 1)[0].strip()
    if not token:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_\-=]+", token):
        return ""
    return token


def _extract_username_from_content_disposition(raw_header: str) -> str:
    header = str(raw_header or "")
    if not header:
        return ""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', header, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _build_extend_payload_from_user(*, user: dict[str, Any], days: int, gb: int) -> dict[str, Any]:
    now = int(time.time())
    current_expire = int(user.get("expire") or 0)
    current_limit = int(user.get("data_limit") or 0)
    target_expire = max(now, current_expire) + days * 86400 if days > 0 else current_expire
    if gb <= 0:
        target_limit = 0
    else:
        base_limit = gb * bot.BYTES_IN_GB
        target_limit = max(current_limit, base_limit) if current_limit > 0 else base_limit
    return {
        "expire": target_expire,
        "data_limit": target_limit,
        "status": "active",
    }


async def _resolve_renewal_username(
    *,
    marzban: MarzbanClient,
    raw_subscription_url: str,
) -> tuple[str | None, str | None]:
    token = _extract_subscription_token(raw_subscription_url)
    if not token:
        return None, "Укажите корректную ссылку подписки RootVPN для продления."
    try:
        response = await marzban.client.get(
            f"/sub/{token}",
            headers={"accept": "*/*", "user-agent": "RootVPNWebsiteAPI/1.0"},
        )
    except Exception:
        logging.exception("Website renewal resolve failed: token request error")
        return None, "Не удалось проверить ссылку подписки. Повторите позже."
    if response.status_code >= 400:
        return None, "Ссылка подписки недействительна для продления."

    username = _extract_username_from_content_disposition(
        response.headers.get("content-disposition", "")
    )
    if not username:
        return None, "Не удалось определить пользователя по ссылке подписки."

    user = await marzban.get_user(username)
    if not user:
        return None, "Пользователь по ссылке подписки не найден в Marzban."
    return username, None


def _resolve_plan(settings: bot.Settings, plan_key: str) -> bot.Plan | None:
    key = str(plan_key or "").strip().lower()
    if not key:
        return None
    return bot.find_plan(settings, key)


def _is_provider_paid(provider: str, status: str) -> bool:
    if provider == "card":
        return status == "succeeded"
    if provider == "crypto":
        return status == "paid"
    return False


def _human_plan(plan: bot.Plan) -> str:
    return f"{plan.title} ({plan.rub:.0f} RUB)"


def _make_web_username(order_id: str, suffix: int = 0) -> str:
    base = f"web_{order_id[:10]}"
    if suffix <= 0:
        return base
    return f"{base}_{suffix}"


def _absolutize_delivery_link(settings: bot.Settings, link: str) -> str:
    if not link:
        return ""
    if link.startswith(("http://", "https://", "sub://")):
        return link
    if link.startswith("/"):
        resolved = bot._absolutize_subscription_link(link, settings.subscription_public_base_url)
        return resolved or link
    return link


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _build_delivery_payload(settings: bot.Settings, user: dict[str, Any]) -> dict[str, Any]:
    subscription_candidates = [
        _absolutize_delivery_link(settings, x)
        for x in bot.extract_subscription_links(
            user,
            public_base_url=settings.subscription_public_base_url,
        )
    ]
    subscription_links = _uniq(subscription_candidates)
    direct_links = _uniq([x for x in bot.extract_links(user) if x])
    return {
        "subscription_url": subscription_links[0] if subscription_links else "",
        "subscription_links": subscription_links,
        "direct_links": direct_links,
    }


async def _notify_admin_web_order_paid(
    *,
    settings: bot.Settings,
    order: dict[str, Any],
    marzban_username: str,
) -> None:
    if not settings.bot_token or not settings.admin_ids:
        return

    order_id = str(order.get("order_id") or "")
    provider = str(order.get("provider") or "")
    plan_key = str(order.get("plan_key") or "")
    amount_rub = float(order.get("amount_rub") or 0)
    contact = str(order.get("customer_contact") or "").strip()
    external_id = str(order.get("external_id") or "")

    lines = [
        "🌐 Оплата через сайт подтверждена",
        f"Order ID: <code>{html.escape(order_id)}</code>",
        f"Провайдер: {html.escape(provider)}",
        f"Сумма: {amount_rub:.2f} RUB",
        f"План: {html.escape(plan_key)}",
        f"Marzban: <code>{html.escape(marzban_username)}</code>",
    ]
    if external_id:
        lines.append(f"External ID: <code>{html.escape(external_id)}</code>")
    if contact:
        lines.append(f"Контакт: {html.escape(contact)}")
    text = "\n".join(lines)

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for admin_id in settings.admin_ids:
            try:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": int(admin_id),
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code >= 400:
                    logging.error(
                        "Website notify: Telegram HTTP error for admin %s: %s %s",
                        admin_id,
                        resp.status_code,
                        (resp.text or "")[:400],
                    )
                    continue
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict) and payload.get("ok") is False:
                    logging.error(
                        "Website notify: Telegram API rejected message for admin %s: %s",
                        admin_id,
                        payload.get("description"),
                    )
            except Exception:
                logging.exception("Website notify: failed to send web payment to admin %s", admin_id)


async def _ensure_web_access(
    *,
    settings: bot.Settings,
    repo: Repo,
    marzban: MarzbanClient,
    order: dict[str, Any],
) -> dict[str, Any]:
    order_id = str(order["order_id"])
    days = int(order.get("days") or 0)
    gb = int(order.get("gb") or 0)
    existing_username = str(order.get("marzban_username") or "").strip()
    if existing_username:
        existing_user = await marzban.get_user(existing_username)
        if existing_user:
            if str(order.get("status") or "") != "paid_applied":
                payload = _build_extend_payload_from_user(user=existing_user, days=days, gb=gb)
                updated = await marzban.modify_user(existing_username, payload)
                await repo.set_web_order_status(order_id, "paid_applied")
                await repo.log_event(
                    event_type="web_order_paid_applied",
                    event_value=str(order.get("provider") or ""),
                    event_meta={
                        "order_id": order_id,
                        "external_id": str(order.get("external_id") or ""),
                        "plan_key": str(order.get("plan_key") or ""),
                        "marzban_username": existing_username,
                        "renewal": True,
                    },
                )
                return {
                    "username": existing_username,
                    "user": updated,
                }
            return {
                "username": existing_username,
                "user": existing_user,
            }

    username = _make_web_username(order_id)
    for i in range(0, 30):
        candidate = _make_web_username(order_id, suffix=i)
        if not await marzban.get_user(candidate):
            username = candidate
            break

    expire = int(time.time()) + days * 86400 if days > 0 else 0
    data_limit = gb * bot.BYTES_IN_GB if gb > 0 else 0

    created = await marzban.create_user(
        username=username,
        expire=expire,
        data_limit=data_limit,
    )
    await repo.attach_web_order_access(order_id=order_id, marzban_username=username)
    await repo.set_web_order_status(order_id, "paid_applied")
    await repo.log_event(
        event_type="web_order_paid_applied",
        event_value=str(order.get("provider") or ""),
        event_meta={
            "order_id": order_id,
            "external_id": str(order.get("external_id") or ""),
            "plan_key": str(order.get("plan_key") or ""),
            "marzban_username": username,
            "renewal": False,
        },
    )
    return {
        "username": username,
        "user": created,
    }


async def create_app() -> web.Application:
    settings = bot.Settings.load()
    runtime = WebsiteRuntime.load(settings)
    repo = Repo(settings.db_path)
    await repo.open()
    marzban = MarzbanClient(settings)

    app = web.Application()
    app["settings"] = settings
    app["runtime"] = runtime
    app["repo"] = repo
    app["marzban"] = marzban

    async def on_cleanup(_app: web.Application) -> None:
        await marzban.close()
        await repo.close()

    app.on_cleanup.append(on_cleanup)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "website_api"})

    async def plans(_request: web.Request) -> web.Response:
        data = [
            {
                "key": plan.key,
                "title": plan.title,
                "days": plan.days,
                "gb": plan.gb,
                "rub": plan.rub,
            }
            for plan in settings.plans
        ]
        providers: list[str] = []
        if settings.yookassa_enabled():
            providers.append("card")
        if runtime.enable_crypto and settings.cryptobot_enabled():
            providers.append("crypto")
        return web.json_response(
            {
                "ok": True,
                "plans": data,
                "providers": providers,
                "support_url": runtime.support_url,
            }
        )

    async def instructions(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "steps": [
                    "Выберите тариф и оплатите заказ.",
                    "Нажмите «Проверить оплату».",
                    "Скопируйте ссылку подписки и импортируйте в VPN-клиент.",
                    "Для Happ (iOS): если не импортируется, Settings -> Dev Settings -> Enable for all configurations.",
                ],
                "support_url": runtime.support_url,
            }
        )

    async def checkout(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        plan_key = str(payload.get("plan_key") or "").strip().lower()
        plan = _resolve_plan(settings, plan_key)
        if plan is None:
            return _json_error("Неизвестный тариф", status=400)

        provider = str(payload.get("provider") or "card").strip().lower()
        if provider not in {"card", "crypto"}:
            return _json_error("Неизвестный способ оплаты", status=400)
        if provider == "card" and not settings.yookassa_enabled():
            return _json_error("Оплата картой сейчас недоступна", status=503)
        if provider == "crypto" and (not runtime.enable_crypto or not settings.cryptobot_enabled()):
            return _json_error("Crypto-оплата сейчас недоступна", status=503)

        order_id = uuid4().hex
        contact = _normalize_contact(payload.get("contact"))
        renew_subscription_url = _normalize_subscription_url(payload.get("renew_subscription_url"))
        renew_username = ""
        if renew_subscription_url:
            renew_username, renew_error = await _resolve_renewal_username(
                marzban=marzban,
                raw_subscription_url=renew_subscription_url,
            )
            if renew_error:
                return _json_error(renew_error, status=400)
        description = f"RootVPN web: {_human_plan(plan)}"

        try:
            if provider == "card":
                external_id, pay_url = await yookassa_create_payment(
                    settings,
                    telegram_id=0,
                    amount_rub=plan.rub,
                    description=description,
                    return_url=f"{runtime.public_url}/?order={order_id}",
                )
            else:
                external_id, pay_url = await cryptobot_create_invoice(
                    settings,
                    telegram_id=0,
                    amount_rub=plan.rub,
                    description=description,
                )
        except Exception as exc:
            logging.exception("Website checkout payment create failed")
            return _json_error(f"Не удалось создать платеж: {exc}", status=502)

        try:
            await repo.create_web_order(
                order_id=order_id,
                provider=provider,
                external_id=external_id,
                status="pending",
                plan_key=plan.key,
                days=plan.days,
                gb=plan.gb,
                amount_rub=plan.rub,
                customer_contact=contact,
                pay_url=pay_url,
            )
            if renew_username:
                await repo.attach_web_order_access(
                    order_id=order_id,
                    marzban_username=renew_username,
                )
            await repo.log_event(
                event_type="web_order_created",
                event_value=provider,
                event_meta={
                    "order_id": order_id,
                    "external_id": external_id,
                    "plan_key": plan.key,
                    "amount_rub": plan.rub,
                    "renewal": bool(renew_username),
                    "renew_username": renew_username,
                },
            )
        except Exception as exc:
            logging.exception(
                "Website checkout order persist failed: order_id=%s provider=%s external_id=%s",
                order_id,
                provider,
                external_id,
            )
            return _json_error(
                f"Не удалось сохранить заказ в БД. Напишите в поддержку и укажите Order ID: {order_id}. Причина: {exc}",
                status=500,
            )
        return web.json_response(
            {
                "ok": True,
                "order_id": order_id,
                "status": "pending",
                "provider": provider,
                "payment_url": pay_url,
                "renewal": bool(renew_username),
            }
        )

    async def order_status(request: web.Request) -> web.Response:
        order_id = str(request.match_info.get("order_id") or "").strip().lower()
        if not order_id:
            return _json_error("Пустой order_id", status=400)
        order = await repo.get_web_order(order_id)
        if not order:
            return _json_error("Заказ не найден", status=404)

        status = str(order.get("status") or "pending")
        previous_status = status
        provider = str(order.get("provider") or "")

        if status not in {"paid_applied", "canceled", "expired", "failed"}:
            try:
                if provider == "card":
                    provider_status = await yookassa_check_payment(settings, str(order["external_id"]))
                elif provider == "crypto":
                    provider_status = await cryptobot_check_invoice(settings, str(order["external_id"]))
                else:
                    provider_status = status
            except Exception as exc:
                logging.exception("Website order status check failed: %s", order_id)
                return _json_error(f"Не удалось проверить оплату: {exc}", status=502)

            status = provider_status
            await repo.set_web_order_status(order_id, status)
            order = await repo.get_web_order(order_id) or order

        if _is_provider_paid(provider, status) or status == "paid_applied":
            try:
                issued = await _ensure_web_access(
                    settings=settings,
                    repo=repo,
                    marzban=marzban,
                    order=order,
                )
            except Exception as exc:
                logging.exception("Website order access provision failed: %s", order_id)
                return _json_error(f"Оплата принята, но выдача доступа не удалась: {exc}", status=502)

            if previous_status != "paid_applied":
                try:
                    latest_order = await repo.get_web_order(order_id) or order
                    await _notify_admin_web_order_paid(
                        settings=settings,
                        order=latest_order,
                        marzban_username=str(issued["username"]),
                    )
                except Exception:
                    logging.exception("Website notify: failed for order %s", order_id)

            delivery = _build_delivery_payload(settings, issued["user"])
            tg_bind_payload = bot.build_web_bind_payload(order_id, bot_token=settings.bot_token)
            tg_bind_url = (
                f"https://t.me/{runtime.bot_username}?start={tg_bind_payload}"
                if runtime.bot_username
                else ""
            )
            return web.json_response(
                {
                    "ok": True,
                    "order_id": order_id,
                    "status": "paid_applied",
                    "marzban_username": issued["username"],
                    "renewal": bool(order.get("marzban_username")),
                    "subscription_url": delivery["subscription_url"],
                    "subscription_links": delivery["subscription_links"],
                    "direct_links": delivery["direct_links"],
                    "tg_bind_payload": tg_bind_payload,
                    "tg_bind_url": tg_bind_url,
                    "support_url": runtime.support_url,
                }
            )

        return web.json_response(
            {
                "ok": True,
                "order_id": order_id,
                "status": status,
                "provider": provider,
                "payment_url": str(order.get("pay_url") or ""),
                "renewal": bool(order.get("marzban_username")),
            }
        )

    def _route(prefix: str, suffix: str) -> str:
        return f"{prefix}{suffix}" if prefix else suffix

    # Support both Caddy patterns:
    # 1) handle /api/*  -> backend path keeps /api/*
    # 2) handle_path /api/* -> backend path becomes /*
    for prefix in ("", "/api"):
        app.router.add_get(_route(prefix, "/health"), health)
        app.router.add_get(_route(prefix, "/plans"), plans)
        app.router.add_get(_route(prefix, "/instructions"), instructions)
        app.router.add_post(_route(prefix, "/checkout"), checkout)
        app.router.add_get(_route(prefix, "/order/{order_id}"), order_status)
    return app


async def _run() -> None:
    settings = bot.Settings.load()
    runtime = WebsiteRuntime.load(settings)
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, runtime.host, runtime.port)
    await site.start()
    logging.info("Website API started on %s:%s", runtime.host, runtime.port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
