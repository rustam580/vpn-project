from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

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
        return WebsiteRuntime(
            host=host,
            port=port,
            public_url=public_url,
            support_url=support_url,
            enable_crypto=enable_crypto,
        )


def _json_error(message: str, *, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def _normalize_contact(raw: Any) -> str:
    value = str(raw or "").strip()
    return value[:160]


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
            mode=settings.config_delivery_mode,
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


async def _ensure_web_access(
    *,
    settings: bot.Settings,
    repo: Repo,
    marzban: MarzbanClient,
    order: dict[str, Any],
) -> dict[str, Any]:
    existing_username = str(order.get("marzban_username") or "").strip()
    if existing_username:
        existing_user = await marzban.get_user(existing_username)
        if existing_user:
            return {
                "username": existing_username,
                "user": existing_user,
            }

    order_id = str(order["order_id"])
    username = _make_web_username(order_id)
    for i in range(0, 30):
        candidate = _make_web_username(order_id, suffix=i)
        if not await marzban.get_user(candidate):
            username = candidate
            break

    days = int(order.get("days") or 0)
    gb = int(order.get("gb") or 0)
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
        await repo.log_event(
            event_type="web_order_created",
            event_value=provider,
            event_meta={
                "order_id": order_id,
                "external_id": external_id,
                "plan_key": plan.key,
                "amount_rub": plan.rub,
            },
        )
        return web.json_response(
            {
                "ok": True,
                "order_id": order_id,
                "status": "pending",
                "provider": provider,
                "payment_url": pay_url,
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

            delivery = _build_delivery_payload(settings, issued["user"])
            return web.json_response(
                {
                    "ok": True,
                    "order_id": order_id,
                    "status": "paid_applied",
                    "marzban_username": issued["username"],
                    "subscription_url": delivery["subscription_url"],
                    "subscription_links": delivery["subscription_links"],
                    "direct_links": delivery["direct_links"],
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
            }
        )

    app.router.add_get("/api/health", health)
    app.router.add_get("/api/plans", plans)
    app.router.add_get("/api/instructions", instructions)
    app.router.add_post("/api/checkout", checkout)
    app.router.add_get("/api/order/{order_id}", order_status)
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
