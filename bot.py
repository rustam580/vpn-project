
import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
from app_texts import (
    build_config_import_hint_text,
    build_quick_connect_guide_text,
    build_start_text,
    build_support_templates_text,
    build_user_faq_text,
)
from payments_service import (
    cryptobot_check_invoice as ps_cryptobot_check_invoice,
    cryptobot_create_invoice as ps_cryptobot_create_invoice,
    yookassa_check_payment as ps_yookassa_check_payment,
    yookassa_create_payment as ps_yookassa_create_payment,
)
from payment_flow import (
    apply_paid_payment as pf_apply_paid_payment,
    check_and_apply_payment as pf_check_and_apply_payment,
)
from bot_formatters import (
    admin_plans_text,
    format_expire,
    format_last_online,
    format_limit,
    format_time_left,
    format_used,
    plan_gb_text,
    plan_title,
    plans_list_text,
)
from bot_handlers_admin import AdminMessageDeps, register_admin_message_handlers
from bot_handlers_callbacks_user import (
    UserCallbackDeps,
    register_user_callback_handlers,
)
from bot_handlers_callbacks_admin import (
    AdminCallbackDeps,
    register_admin_callback_handlers,
)
from bot_handlers_fallback import FallbackDeps, register_fallback_handler
from bot_handlers_user import UserMessageDeps, register_user_message_handlers
from bot_keyboards import (
    admin_panel_keyboard,
    admin_plans_keyboard,
    broadcast_confirm_keyboard,
    broadcast_format_label,
    broadcast_next_format,
    broadcast_parse_mode,
    buy_plan_keyboard,
    buy_target_keyboard,
    device_methods_keyboard,
    keyboard_for_user,
    more_actions_keyboard,
    pay_action_keyboard,
    payment_methods_keyboard,
    renewal_actions_keyboard,
)
from bot_access import (
    ensure_device,
    ensure_user,
    extend_access,
    extend_access_all_devices,
    extend_access_days_only,
    extend_access_device,
    set_permanent_access,
    sync_expire_across_devices,
)
from bot_access import apply_referral_bonus_if_needed as _apply_referral_bonus_if_needed
from bot_marzban import MarzbanClient
from bot_network import _parse_sar_dev_output
from bot_ops import (
    build_admin_stats_text,
    build_ops_report_text,
    build_payments_summary,
    build_ref_top_text,
)
from bot_rate_limit import InMemoryRateLimiter
from bot_repo import Repo
from bot_router_helpers import (
    BroadcastPreviewContext,
    UserLookupContext,
    send_broadcast_preview as send_broadcast_preview_impl,
    send_user_lookup as send_user_lookup_impl,
)
from bot_workers import (
    auto_renew_plan as _auto_renew_plan,
    auto_renew_provider as _auto_renew_provider,
    cryptobot_auto_worker as _cryptobot_auto_worker,
    subscription_renewal_worker as _subscription_renewal_worker,
    yookassa_auto_worker as _yookassa_auto_worker,
)

BYTES_IN_GB = 1024**3
DEPLOY_REPORT_PATH = Path("/opt/vpn-bot/deploy/last-deploy.log")
DEPLOY_REPORT_TTL_SEC = 3600
WORKER_ALERT_PREVIEW_LIMIT = 800
_worker_alert_last_sent: dict[str, float] = {}
_worker_alert_lock = asyncio.Lock()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_int_csv(raw: str, *, default: tuple[int, ...]) -> tuple[int, ...]:
    values: set[int] = set()
    for part in str(raw or "").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value > 0:
            values.add(value)
    if not values:
        return default
    return tuple(sorted(values))


def normalize_config_delivery_mode(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"direct", "subscription_first", "subscription_only"}:
        return value
    return "direct"


def normalize_public_base_url(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        return ""
    return value.rstrip("/")


def _absolutize_subscription_link(link: str, public_base_url: str) -> str:
    item = link.strip()
    if not item:
        return ""
    if item.startswith(("http://", "https://", "sub://")):
        return item
    base = normalize_public_base_url(public_base_url)
    if not base:
        return ""
    if item.startswith("/"):
        return f"{base}{item}"
    return f"{base}/{item}"




def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            result.add(int(part))
    return result


def _list_iface_names() -> list[str]:
    try:
        lines = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    names: list[str] = []
    for line in lines[2:]:
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if not name or name == "lo":
            continue
        names.append(name)
    return names


def _detect_default_iface() -> str | None:
    try:
        lines = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[0].strip()
        destination = parts[1].strip()
        flags_raw = parts[3].strip()
        try:
            flags = int(flags_raw, 16)
        except ValueError:
            continue
        if destination == "00000000" and (flags & 0x1) and iface and iface != "lo":
            return iface
    candidates = _list_iface_names()
    return candidates[0] if candidates else None


def _resolve_net_iface(configured_iface: str) -> str:
    configured = configured_iface.strip()
    iface_names = set(_list_iface_names())
    if configured and configured in iface_names:
        return configured
    detected = _detect_default_iface()
    if detected:
        return detected
    if configured:
        return configured
    return "lo"


def _detect_port_speed_mbps(iface: str) -> float | None:
    speed_file = Path(f"/sys/class/net/{iface}/speed")
    try:
        raw = speed_file.read_text(encoding="utf-8").strip()
        speed = float(raw)
        if 0 < speed < 1_000_000:
            return speed
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["ethtool", iface],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    m = re.search(r"Speed:\s*([0-9.]+)\s*Mb/s", out)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val if val > 0 else None


def _resolve_port_speed_mbps(raw: str, iface: str) -> float:
    normalized = raw.strip().lower()
    if normalized in {"", "auto"}:
        return _detect_port_speed_mbps(iface) or 100.0
    try:
        value = float(raw)
    except ValueError:
        return _detect_port_speed_mbps(iface) or 100.0
    if value > 0:
        return value
    return _detect_port_speed_mbps(iface) or 100.0


@dataclass(frozen=True)
class Plan:
    key: str
    title: str
    days: int
    gb: int
    rub: float


def _default_plan_title(days: int) -> str:
    if days == 30:
        return "1 месяц"
    if days == 90:
        return "3 месяца"
    if days == 365:
        return "12 месяцев"
    return f"{days} дней"


def _normalize_plan_key(raw: str, fallback_days: int) -> str:
    key = re.sub(r"[^a-zA-Z0-9_-]+", "", raw.strip().lower())
    if key:
        return key[:20]
    return f"{fallback_days}d"


def _default_plan(*, days: int, gb: int, rub: float) -> tuple[Plan, ...]:
    return (
        Plan(
            key=f"{max(days, 1)}d",
            title=_default_plan_title(max(days, 1)),
            days=max(days, 1),
            gb=max(gb, 0),
            rub=max(rub, 0.01),
        ),
    )


def _parse_plans_json(raw: str, *, default_days: int, default_gb: int, default_rub: float) -> tuple[Plan, ...]:
    if not raw.strip():
        return _default_plan(days=default_days, gb=default_gb, rub=default_rub)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("PLANS_JSON must be a valid JSON array") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("PLANS_JSON must be a non-empty JSON array")

    plans: list[Plan] = []
    seen_keys: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each plan in PLANS_JSON must be an object")
        days = int(item.get("days", 0))
        gb = int(item.get("gb", default_gb))
        rub_raw = item.get("rub", item.get("price_rub"))
        if rub_raw is None:
            raise ValueError(f"Plan {item!r} must define 'rub' (or 'price_rub')")
        rub = float(rub_raw)
        if days <= 0:
            raise ValueError(f"Invalid plan days in {item!r}")
        if rub <= 0:
            raise ValueError(f"Invalid plan price in {item!r}")
        key = _normalize_plan_key(str(item.get("key", "")), days)
        if key in seen_keys:
            raise ValueError(f"Duplicate plan key: {key}")
        seen_keys.add(key)
        title = str(item.get("title", "")).strip() or _default_plan_title(days)
        plans.append(Plan(key=key, title=title, days=days, gb=max(gb, 0), rub=rub))

    return tuple(plans)


def _plans_to_json(plans: tuple[Plan, ...]) -> str:
    payload = [
        {
            "key": plan.key,
            "title": plan_title(plan),
            "days": plan.days,
            "gb": plan.gb,
            "rub": float(plan.rub),
        }
        for plan in plans
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _preset_plans(preset_key: str) -> tuple[Plan, ...] | None:
    if preset_key == "balance":
        return (
            Plan(key="m1", title="1 месяц", days=30, gb=0, rub=99.0),
            Plan(key="m3", title="3 месяца", days=90, gb=0, rub=259.0),
            Plan(key="y1", title="12 месяцев", days=365, gb=0, rub=949.0),
        )
    if preset_key == "margin":
        return (
            Plan(key="m1", title="1 месяц", days=30, gb=0, rub=99.0),
            Plan(key="m3", title="3 месяца", days=90, gb=0, rub=279.0),
            Plan(key="y1", title="12 месяцев", days=365, gb=0, rub=1099.0),
        )
    if preset_key == "convert":
        return (
            Plan(key="m1", title="1 месяц", days=30, gb=0, rub=99.0),
            Plan(key="m3", title="3 месяца", days=90, gb=0, rub=239.0),
            Plan(key="y1", title="12 месяцев", days=365, gb=0, rub=849.0),
        )
    return None


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: set[int]
    marzban_base_url: str
    marzban_username: str
    marzban_password: str
    marzban_verify_ssl: bool
    marzban_proxy_protocol: str
    config_delivery_mode: str
    subscription_public_base_url: str
    trial_days: int
    trial_gb: int
    plans: tuple[Plan, ...]
    pay_days: int
    pay_gb: int
    pay_rub: float
    cryptobot_token: str
    cryptobot_testnet: bool
    cryptobot_fiat: str
    cryptobot_accepted_assets: str
    cryptobot_expires_in: int
    cryptobot_poll_seconds: int
    yookassa_poll_seconds: int
    payment_processing_requeue_seconds: int
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_return_url: str
    user_rate_limit_count: int
    user_rate_limit_window_sec: int
    callback_rate_limit_count: int
    callback_rate_limit_window_sec: int
    support_username: str
    support_text: str
    channel_url: str
    referral_bonus_days: int
    device_limit: int
    device_add_rub: float
    deploy_broadcast_users: bool
    db_path: str
    ops_report_enabled: bool
    ops_report_hour: int
    ops_report_minute: int
    net_iface: str
    port_speed_mbps: float
    port_utilization: float
    concurrency_ratio: float
    renewal_alerts_enabled: bool
    renewal_alert_interval_sec: int
    renewal_reminder_hours: tuple[int, ...]
    renewal_expired_alert_enabled: bool
    admin_alerts_enabled: bool
    admin_alert_cooldown_sec: int
    auto_renew_invoice_enabled: bool
    auto_renew_invoice_hours_before: int
    auto_renew_invoice_provider: str
    auto_renew_invoice_plan_key: str
    auto_renew_invoice_target: str

    @staticmethod
    def load() -> "Settings":
        load_dotenv()
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        admin_raw = os.getenv("BOT_ADMIN_IDS", "").strip()
        marzban_base_url = os.getenv("MARZBAN_BASE_URL", "").strip().rstrip("/")
        marzban_username = os.getenv("MARZBAN_USERNAME", "").strip()
        marzban_password = os.getenv("MARZBAN_PASSWORD", "").strip()
        net_iface = _resolve_net_iface(os.getenv("NET_IFACE", "").strip())
        port_speed_mbps = _resolve_port_speed_mbps(
            os.getenv("PORT_SPEED_Mbps", "auto").strip(),
            net_iface,
        )

        if not bot_token:
            raise ValueError("BOT_TOKEN is required")
        if not admin_raw:
            raise ValueError("BOT_ADMIN_IDS is required")
        if not marzban_base_url:
            raise ValueError("MARZBAN_BASE_URL is required")
        if not marzban_username or not marzban_password:
            raise ValueError("MARZBAN_USERNAME and MARZBAN_PASSWORD are required")
        default_pay_days = int(os.getenv("PAY_DAYS", "30"))
        default_pay_gb = int(os.getenv("PAY_GB", "0"))
        default_pay_rub = float(os.getenv("PAY_RUB", "99"))
        plans_raw = os.getenv("PLANS_JSON", "")
        try:
            plans = _parse_plans_json(
                plans_raw,
                default_days=default_pay_days,
                default_gb=default_pay_gb,
                default_rub=default_pay_rub,
            )
        except ValueError as exc:
            logging.warning("Invalid PLANS_JSON, fallback to PAY_* defaults: %s", exc)
            plans = _default_plan(days=default_pay_days, gb=default_pay_gb, rub=default_pay_rub)
        base_plan = plans[0]
        reminder_hours = parse_int_csv(
            os.getenv("RENEWAL_REMINDER_HOURS", "72,24,6"),
            default=(6, 24, 72),
        )
        auto_provider = os.getenv("AUTO_RENEW_INVOICE_PROVIDER", "card").strip().lower()
        if auto_provider not in {"card", "crypto"}:
            auto_provider = "card"
        auto_target = os.getenv("AUTO_RENEW_INVOICE_TARGET", "all").strip().lower()
        if auto_target not in {"all", "slot"}:
            auto_target = "all"

        return Settings(
            bot_token=bot_token,
            admin_ids=parse_admin_ids(admin_raw),
            marzban_base_url=marzban_base_url,
            marzban_username=marzban_username,
            marzban_password=marzban_password,
            marzban_verify_ssl=env_bool("MARZBAN_VERIFY_SSL", True),
            marzban_proxy_protocol=os.getenv("MARZBAN_PROXY_PROTOCOL", "vless").strip().lower(),
            config_delivery_mode=normalize_config_delivery_mode(
                os.getenv("CONFIG_DELIVERY_MODE", "direct")
            ),
            subscription_public_base_url=normalize_public_base_url(
                os.getenv("SUBSCRIPTION_PUBLIC_BASE_URL", "")
            ),
            trial_days=int(os.getenv("TRIAL_DAYS", "1")),
            trial_gb=int(os.getenv("TRIAL_GB", "0")),
            plans=plans,
            pay_days=base_plan.days,
            pay_gb=base_plan.gb,
            pay_rub=base_plan.rub,
            cryptobot_token=os.getenv("CRYPTOBOT_TOKEN", "").strip(),
            cryptobot_testnet=env_bool("CRYPTOBOT_TESTNET", False),
            cryptobot_fiat=os.getenv("CRYPTOBOT_FIAT", "RUB").strip().upper(),
            cryptobot_accepted_assets=os.getenv("CRYPTOBOT_ACCEPTED_ASSETS", "USDT,TON").strip(),
            cryptobot_expires_in=int(os.getenv("CRYPTOBOT_EXPIRES_IN", "3600")),
            cryptobot_poll_seconds=int(os.getenv("CRYPTOBOT_POLL_SECONDS", "45")),
            yookassa_shop_id=os.getenv("YOOKASSA_SHOP_ID", "").strip(),
            yookassa_secret_key=os.getenv("YOOKASSA_SECRET_KEY", "").strip(),
            yookassa_return_url=os.getenv("YOOKASSA_RETURN_URL", "https://t.me").strip(),
            yookassa_poll_seconds=int(os.getenv("YOOKASSA_POLL_SECONDS", "60")),
            payment_processing_requeue_seconds=max(
                60, int(os.getenv("PAYMENT_PROCESSING_REQUEUE_SECONDS", "600"))
            ),
            user_rate_limit_count=int(os.getenv("USER_RATE_LIMIT_COUNT", "12")),
            user_rate_limit_window_sec=int(os.getenv("USER_RATE_LIMIT_WINDOW_SEC", "30")),
            callback_rate_limit_count=int(os.getenv("CALLBACK_RATE_LIMIT_COUNT", "20")),
            callback_rate_limit_window_sec=int(os.getenv("CALLBACK_RATE_LIMIT_WINDOW_SEC", "30")),
            support_username=os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
            support_text=os.getenv("SUPPORT_TEXT", "Напишите нам, поможем с подключением и оплатой.").strip(),
            channel_url=os.getenv("CHANNEL_URL", "").strip(),
            referral_bonus_days=int(os.getenv("REFERRAL_BONUS_DAYS", "3")),
            device_limit=int(os.getenv("DEVICE_LIMIT", "1")),
            device_add_rub=float(os.getenv("DEVICE_ADD_RUB", "99")),
            deploy_broadcast_users=env_bool("DEPLOY_BROADCAST_USERS", False),
            db_path=os.getenv("DB_PATH", "./data/bot.sqlite3").strip(),
            ops_report_enabled=env_bool("OPS_REPORT_ENABLED", True),
            ops_report_hour=max(0, min(23, int(os.getenv("OPS_REPORT_HOUR", "9")))),
            ops_report_minute=max(0, min(59, int(os.getenv("OPS_REPORT_MINUTE", "0")))),
            net_iface=net_iface,
            port_speed_mbps=port_speed_mbps,
            port_utilization=float(os.getenv("PORT_UTILIZATION", "0.8")),
            concurrency_ratio=float(os.getenv("CONCURRENCY_RATIO", "0.05")),
            renewal_alerts_enabled=env_bool("RENEWAL_ALERTS_ENABLED", True),
            renewal_alert_interval_sec=max(60, int(os.getenv("RENEWAL_ALERT_INTERVAL_SEC", "300"))),
            renewal_reminder_hours=reminder_hours,
            renewal_expired_alert_enabled=env_bool("RENEWAL_EXPIRED_ALERT_ENABLED", True),
            admin_alerts_enabled=env_bool("ADMIN_ALERTS_ENABLED", True),
            admin_alert_cooldown_sec=max(0, int(os.getenv("ADMIN_ALERT_COOLDOWN_SEC", "900"))),
            auto_renew_invoice_enabled=env_bool("AUTO_RENEW_INVOICE_ENABLED", False),
            auto_renew_invoice_hours_before=max(
                1, int(os.getenv("AUTO_RENEW_INVOICE_HOURS_BEFORE", "12"))
            ),
            auto_renew_invoice_provider=auto_provider,
            auto_renew_invoice_plan_key=os.getenv("AUTO_RENEW_INVOICE_PLAN_KEY", "").strip(),
            auto_renew_invoice_target=auto_target,
        )

    def cryptobot_enabled(self) -> bool:
        return bool(self.cryptobot_token)

    def yookassa_enabled(self) -> bool:
        return bool(self.yookassa_shop_id and self.yookassa_secret_key and self.yookassa_return_url)


async def broadcast_menu_update(
    *,
    bot: Bot,
    settings: Settings,
    repo: "Repo",
    force: bool = False,
) -> tuple[int, int, int, list[str]]:
    if not force and not settings.deploy_broadcast_users:
        return (0, 0, 0, [])
    try:
        targets = set(await repo.list_known_telegram_ids())
        targets.update(settings.admin_ids)
        if not targets:
            return (0, 0, 0, [])
        text = "⚙️ Обновление завершено. Кнопки обновлены."
        logging.info("Menu broadcast started: force=%s, targets=%s", force, len(targets))
        sent = 0
        failed = 0
        fail_samples: list[str] = []
        for tg_id in targets:
            try:
                await bot.send_message(
                    tg_id,
                    text,
                    reply_markup=keyboard_for_user(is_admin=is_admin(tg_id, settings)),
                )
                sent += 1
            except Exception:
                failed += 1
                if len(fail_samples) < 5:
                    fail_samples.append(str(tg_id))
                logging.exception("Deploy broadcast: failed to send to %s", tg_id)
            await asyncio.sleep(0.05)
        summary_lines = [f"📣 Обновление меню: доставлено {sent}/{len(targets)}, ошибок {failed}."]
        if fail_samples:
            summary_lines.append("Примеры ID с ошибкой: " + ", ".join(fail_samples))
        summary = "\n".join(summary_lines)
        logging.info("Menu broadcast finished: %s", summary.replace("\n", " | "))
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(int(admin_id), summary)
            except Exception:
                logging.exception("Deploy broadcast summary failed for admin %s", admin_id)
        return (sent, len(targets), failed, fail_samples)
    except Exception:
        logging.exception("Deploy broadcast failed")
        return (0, 0, 0, [])


async def send_deploy_report_if_any(bot: Bot, settings: Settings, repo: "Repo | None" = None) -> None:
    path = DEPLOY_REPORT_PATH
    should_delete = False
    try:
        if not path.exists():
            return
        age = time.time() - path.stat().st_mtime
        if age > DEPLOY_REPORT_TTL_SEC:
            should_delete = True
            return
        text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="ignore")
        if not text.strip():
            return
        if "exit=" not in text:
            return
        lines = [ln.rstrip() for ln in text.splitlines()]
        exit_code = None
        started_at = None
        for ln in lines:
            if ln.startswith("Deploy started:"):
                started_at = ln.split("Deploy started:", 1)[1].strip()
            if ln.startswith("exit="):
                try:
                    exit_code = int(ln.split("=", 1)[1].strip())
                except ValueError:
                    exit_code = None

        def _is_noise(line: str) -> bool:
            return (
                "HTTP Request: GET http://127.0.0.1:8000/api/" in line
                or "INFO | httpx | HTTP Request: GET" in line
                or "INFO | aiogram.dispatcher | Run polling" in line
                or "INFO | aiogram.dispatcher | Start polling" in line
                or "Polling stopped" in line
            )

        def _is_error_line(line: str) -> bool:
            low = line.lower()
            return (
                "error" in low
                or "exception" in low
                or "traceback" in low
                or "syntaxerror" in low
                or "indentationerror" in low
                or "taberror" in low
            )

        syntax_markers = ("SyntaxError", "IndentationError", "TabError")
        syntax_idx = None
        for i, ln in enumerate(lines):
            if any(m in ln for m in syntax_markers):
                syntax_idx = i
                break

        if syntax_idx is not None:
            start = max(0, syntax_idx - 3)
            end = min(len(lines), syntax_idx + 2)
            snippet = "\n".join(lines[start:end]).strip()
            msg_lines = [
                "❌ Deploy: Syntax error",
                "Статус: FAIL (syntax)",
            ]
            if started_at:
                msg_lines.append(f"Время: {started_at}")
            if snippet:
                msg_lines.append("")
                msg_lines.append("Фрагмент:")
                msg_lines.append(snippet)
            msg = "\n".join(msg_lines)
            if len(msg) > 3500:
                msg = msg[:3500] + "\n..."
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(int(admin_id), msg)
                except Exception:
                    logging.exception("Failed to send deploy report to admin %s", admin_id)
            should_delete = True
            return

        status = "OK" if exit_code == 0 else f"FAIL (exit {exit_code})"
        header = "✅ Deploy: OK" if exit_code == 0 else "❌ Deploy: FAIL"
        filtered = [ln for ln in lines if not _is_noise(ln)]
        tail_lines = filtered[-40:] if filtered else lines[-40:]
        tail_text = "\n".join(tail_lines)
        errors_found = any(_is_error_line(ln) for ln in lines)
        msg_lines = [header, f"Статус: {status}"]
        if started_at:
            msg_lines.append(f"Время: {started_at}")
        if not errors_found:
            msg_lines.append("Ошибки: не найдены")
        msg_lines.append("\nПоследние строки:")
        msg_lines.append(tail_text)
        msg = "\n".join(msg_lines)
        if len(msg) > 3500:
            msg = msg[:3500] + "\n..."
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(int(admin_id), msg)
            except Exception:
                logging.exception("Failed to send deploy report to admin %s", admin_id)
        if exit_code == 0 and repo is not None and settings.deploy_broadcast_users:
            await broadcast_menu_update(bot=bot, settings=settings, repo=repo)
        should_delete = True
    except Exception:
        logging.exception("Failed to read deploy report")
    finally:
        if should_delete:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def is_admin(telegram_id: int | None, settings: Settings) -> bool:
    return telegram_id is not None and telegram_id in settings.admin_ids


def find_plan(settings: Settings, key: str) -> Plan | None:
    normalized = _normalize_plan_key(key, settings.pay_days)
    for plan in settings.plans:
        if plan.key == normalized:
            return plan
    return None


def enabled_payment_providers(settings: Settings) -> list[str]:
    providers: list[str] = []
    if settings.cryptobot_enabled():
        providers.append("crypto")
    if settings.yookassa_enabled():
        providers.append("card")
    return providers


def build_username(telegram_id: int) -> str:
    return f"tg_{telegram_id}"


def build_device_username(telegram_id: int, device_id: int) -> str:
    if device_id <= 1:
        return build_username(telegram_id)
    return f"tg_{telegram_id}_d{device_id}"


def build_replacement_username(telegram_id: int, device_id: int) -> str:
    suffix = uuid4().hex[:8]
    if device_id <= 1:
        return f"tg_{telegram_id}_r{suffix}"
    return f"tg_{telegram_id}_d{device_id}_r{suffix}"


def _link_copy_keyboard(link: str) -> InlineKeyboardMarkup | None:
    return None


def extract_start_payload(text: str | None) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def parse_referrer_from_payload(payload: str) -> int | None:
    if not payload.startswith("ref_"):
        return None
    raw = payload[4:].strip()
    if not raw.isdigit():
        return None
    return int(raw)


def extract_links(user: dict[str, Any]) -> list[str]:
    raw = user.get("links")
    result: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                item = item.strip()
                if item and item not in seen:
                    result.append(item)
                    seen.add(item)
    return result


def extract_subscription_links(
    user: dict[str, Any], *, public_base_url: str = ""
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def _push(value: Any) -> None:
        if not isinstance(value, str):
            return
        item = _absolutize_subscription_link(value, public_base_url)
        if not item:
            return
        if item not in seen:
            seen.add(item)
            result.append(item)

    for candidate in (
        user.get("subscription_url"),
        user.get("subscription_link"),
        user.get("subscription"),
        user.get("sub_link"),
    ):
        _push(candidate)

    extra = user.get("subscription_links")
    if isinstance(extra, list):
        for item in extra:
            _push(item)
    elif isinstance(extra, dict):
        for value in extra.values():
            _push(value)
    return result


def select_delivery_links(
    user: dict[str, Any], *, mode: str, public_base_url: str = ""
) -> list[str]:
    direct = extract_links(user)
    subs = extract_subscription_links(user, public_base_url=public_base_url)
    normalized_mode = normalize_config_delivery_mode(mode)
    if normalized_mode == "subscription_only":
        return subs
    if normalized_mode == "subscription_first":
        return subs if subs else direct
    return direct


def status_text(user: dict[str, Any]) -> str:
    links = extract_links(user)
    expire_ts = int(user.get("expire", 0) or 0)
    status = str(user.get("status", "unknown"))
    status_icon = "🟢" if status == "active" else "⚪"
    status_label = {
        "active": "активен",
        "disabled": "отключен",
        "expired": "истек",
        "limited": "ограничен",
    }.get(status, status)
    cfg_count = 1 if links else 0
    return (
        f"👤 <b>Пользователь:</b> {user.get('username', 'unknown')}\n"
        f"{status_icon} <b>Статус:</b> {status_label}\n"
        f"📊 <b>Трафик:</b> {format_used(int(user.get('used_traffic', 0) or 0))} из {format_limit(int(user.get('data_limit', 0) or 0))}\n"
        f"🗓 <b>Действует до:</b> {format_expire(expire_ts)}\n"
        f"⏳ <b>Осталось:</b> {format_time_left(expire_ts)}\n"
        f"🔗 <b>Конфигов:</b> {cfg_count}"
    )


async def send_status(message: Message, user: dict[str, Any]) -> None:
    await message.answer(status_text(user), parse_mode="HTML")


async def send_status_to_bot(bot: Bot, telegram_id: int, user: dict[str, Any]) -> None:
    await bot.send_message(telegram_id, status_text(user), parse_mode="HTML")


async def send_links(message: Message, user: dict[str, Any]) -> None:
    links = extract_links(user)
    if not links:
        await message.answer("⚠️ Конфиг не найден в ответе Marzban. Попробуйте позже.")
        return
    await message.answer("🔑 Ваша ссылка подключения (1 устройство):")
    link = links[0]
    safe_link = html.escape(link)
    text = f"<code>{safe_link}</code>"
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    await message.answer(config_import_hint_text(), parse_mode="HTML")


def _link_preview(link: str) -> str:
    link = link.strip()
    if len(link) <= 28:
        return link
    prefix = ""
    core = link
    if "://" in link:
        proto, rest = link.split("://", 1)
        prefix = f"{proto}://"
        core = rest
    if len(core) <= 20:
        return link
    return f"{prefix}{core[:10]}...{core[-8:]}"


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


ENV_EDITABLE_KEYS: dict[str, str] = {
    "TRIAL_DAYS": "int",
    "TRIAL_GB": "int",
    "PAY_DAYS": "int",
    "PAY_GB": "int",
    "PAY_RUB": "float",
    "PLANS_JSON": "str",
    "DEVICE_LIMIT": "int",
    "DEVICE_ADD_RUB": "float",
    "REFERRAL_BONUS_DAYS": "int",
    "SUPPORT_USERNAME": "str",
    "SUPPORT_TEXT": "str",
    "CHANNEL_URL": "str",
    "CONFIG_DELIVERY_MODE": "str",
    "SUBSCRIPTION_PUBLIC_BASE_URL": "str",
    "DEPLOY_BROADCAST_USERS": "bool",
    "OPS_REPORT_ENABLED": "bool",
    "OPS_REPORT_HOUR": "int",
    "OPS_REPORT_MINUTE": "int",
    "YOOKASSA_POLL_SECONDS": "int",
    "YOOKASSA_SHOP_ID": "str",
    "YOOKASSA_SECRET_KEY": "str",
    "YOOKASSA_RETURN_URL": "str",
    "PAYMENT_PROCESSING_REQUEUE_SECONDS": "int",
    "RENEWAL_ALERTS_ENABLED": "bool",
    "RENEWAL_ALERT_INTERVAL_SEC": "int",
    "RENEWAL_REMINDER_HOURS": "str",
    "RENEWAL_EXPIRED_ALERT_ENABLED": "bool",
    "ADMIN_ALERTS_ENABLED": "bool",
    "ADMIN_ALERT_COOLDOWN_SEC": "int",
    "AUTO_RENEW_INVOICE_ENABLED": "bool",
    "AUTO_RENEW_INVOICE_HOURS_BEFORE": "int",
    "AUTO_RENEW_INVOICE_PROVIDER": "str",
    "AUTO_RENEW_INVOICE_PLAN_KEY": "str",
    "AUTO_RENEW_INVOICE_TARGET": "str",
}


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


def _configs_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup | None:
    if not items:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    if len(items) <= 2:
        for index, label in items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Показать #{index} ({_short_label(label)})",
                        callback_data=f"cfg:show:{index}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="Показать все в чате", callback_data="cfg:showall")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _devices_rename_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    if not devices:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in devices:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{device_id}. {_short_label(label, limit=22)}",
                    callback_data=f"devrename:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="devrename:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _devices_replace_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    if not devices:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in devices:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{device_id}. {_short_label(label, limit=22)}",
                    callback_data=f"devreplace:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="devreplace:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _device_replace_confirm_keyboard(device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"devreplace_confirm:{device_id}:yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"devreplace_confirm:{device_id}:no",
                ),
            ]
        ]
    )


async def collect_device_links(
    *,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> list[tuple[int, str, str]]:
    devices = await repo.list_devices(telegram_id)
    if not devices:
        _, user, _ = await ensure_device(
            telegram_id=telegram_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=False,
        )
        if not user:
            return []
        links = select_delivery_links(
            user,
            mode=settings.config_delivery_mode,
            public_base_url=settings.subscription_public_base_url,
        )
        label = _device_label(1, None)
        return [(1, label, link) for link in links]

    result: list[tuple[int, str, str]] = []
    for row in devices:
        device_id = int(row["device_id"])
        username = str(row["marzban_username"])
        label = _device_label(device_id, row.get("device_name"))
        user = await marzban.get_user(username)
        if not user:
            continue
        status = str(user.get("status", "unknown"))
        if status != "active":
            continue
        links = select_delivery_links(
            user,
            mode=settings.config_delivery_mode,
            public_base_url=settings.subscription_public_base_url,
        )
        for link in links:
            result.append((device_id, label, link))
    return sorted(result, key=lambda item: (item[0], item[2]))


async def send_device_links(
    *,
    message: Message,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> None:
    items = await collect_device_links(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
    )
    if not items:
        await message.answer("⚠️ Активные ссылки подключения не найдены.")
        return

    await message.answer(
        f"🔑 Ниже ваши активные ссылки для подключения ({len(items)}).\n"
        "Нажмите на ссылку, чтобы скопировать."
    )
    await send_configs_in_chat(message, items)


def _render_config_block(label: str, link: str) -> str:
    safe_label = html.escape(label)
    safe_link = html.escape(link)
    return f"{safe_label}:\n<code>{safe_link}</code>"


async def send_configs_in_chat(message: Message, items: list[tuple[int, str, str]]) -> None:
    if not items:
        await message.answer("⚠️ Активные ссылки подключения не найдены.")
        return
    chunks: list[str] = []
    current = ""
    for _, label, link in items:
        block = _render_config_block(label, link)
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > 3500 and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


async def send_configs_in_chat_to_bot(
    *,
    bot: Bot,
    telegram_id: int,
    items: list[tuple[int, str, str]],
) -> None:
    if not items:
        await bot.send_message(telegram_id, "⚠️ Активные ссылки подключения не найдены.")
        return
    chunks: list[str] = []
    current = ""
    for _, label, link in items:
        block = _render_config_block(label, link)
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > 3500 and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await bot.send_message(telegram_id, chunk, parse_mode="HTML", disable_web_page_preview=True)


async def send_device_links_to_bot(
    *,
    bot: Bot,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> None:
    items = await collect_device_links(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
    )
    if not items:
        await bot.send_message(telegram_id, "⚠️ Активные ссылки подключения не найдены.")
        return
    await bot.send_message(
        telegram_id,
        f"🔑 Ниже ваши активные ссылки для подключения ({len(items)}).\n"
        "Нажмите на ссылку, чтобы скопировать.",
    )
    await send_configs_in_chat_to_bot(bot=bot, telegram_id=telegram_id, items=items)


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

async def cryptobot_create_invoice(
    settings: Settings,
    telegram_id: int,
    *,
    amount_rub: float | None = None,
    description: str | None = None,
) -> tuple[str, str]:
    return await ps_cryptobot_create_invoice(
        settings,
        telegram_id,
        amount_rub=amount_rub,
        description=description,
    )


async def cryptobot_check_invoice(settings: Settings, external_id: str) -> str:
    return await ps_cryptobot_check_invoice(settings, external_id)


async def yookassa_create_payment(
    settings: Settings,
    telegram_id: int,
    *,
    amount_rub: float | None = None,
    description: str | None = None,
) -> tuple[str, str]:
    return await ps_yookassa_create_payment(
        settings,
        telegram_id,
        amount_rub=amount_rub,
        description=description,
    )


async def yookassa_check_payment(settings: Settings, external_id: str) -> str:
    return await ps_yookassa_check_payment(settings, external_id)


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
    return await pf_apply_paid_payment(
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


async def check_and_apply_payment(
    *,
    provider: str,
    external_id: str,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    bot: Bot | None = None,
) -> tuple[str, dict[str, Any] | None]:
    return await pf_check_and_apply_payment(
        provider=provider,
        external_id=external_id,
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        bot=bot,
        cryptobot_check_invoice_fn=cryptobot_check_invoice,
        yookassa_check_payment_fn=yookassa_check_payment,
        apply_paid_payment_fn=apply_paid_payment,
    )


async def notify_access_updated(
    bot: Bot,
    telegram_id: int,
    user: dict[str, Any],
    text: str,
    *,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> None:
    await bot.send_message(telegram_id, text)
    await send_status_to_bot(bot, telegram_id, user)
    await send_device_links_to_bot(
        bot=bot,
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
    )


async def notify_admin_payment(
    *,
    bot: Bot,
    settings: Settings,
    repo: Repo,
    payment: dict[str, Any],
) -> None:
    try:
        tg_id = int(payment.get("telegram_id") or 0)
        if tg_id <= 0:
            return
        provider = str(payment.get("provider") or "")
        external_id = str(payment.get("external_id") or "")
        days = int(payment.get("days") or 0)
        gb = int(payment.get("gb") or 0)
        amount = float(payment.get("amount_rub") or 0)
        purpose = str(payment.get("purpose") or "plan")
        device_slot = payment.get("device_slot")

        chat = None
        try:
            chat = await bot.get_chat(tg_id)
        except Exception:
            chat = None
        name = ""
        username = ""
        if chat is not None:
            name_parts = [chat.first_name or "", chat.last_name or ""]
            name = " ".join(p for p in name_parts if p).strip()
            username = str(chat.username or "").strip()

        marzban_username = ""
        row = await repo.get_user(tg_id)
        if row:
            marzban_username = str(row.get("marzban_username") or "")

        lines = [
            "💳 Оплата подтверждена",
            f"Провайдер: {html.escape(provider)}",
            f"Сумма: {amount:.2f} RUB",
        ]
        if purpose == "device_add":
            slot_text = f", слот {device_slot}" if device_slot else ""
            lines.append(f"Тип: дополнительное устройство{slot_text}")
        else:
            lines.append(
                f"Тариф: {days} дн., {plan_gb_text(gb)}"
            )
        if external_id:
            lines.append(f"Payment ID: {html.escape(external_id)}")
        link = f'<a href="tg://user?id={tg_id}">ID {tg_id}</a>'
        user_line = f"Пользователь: {link}"
        if name:
            user_line += f" ({html.escape(name)})"
        if username:
            user_line += f" @{html.escape(username)}"
        lines.append(user_line)
        if marzban_username:
            lines.append(f"Marzban: {html.escape(marzban_username)}")

        text = "\n".join(lines)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(int(admin_id), text, parse_mode="HTML")
            except Exception:
                logging.exception("Payment notify: failed to send to admin %s", admin_id)
    except Exception:
        logging.exception("Payment notify: failed to build admin message")


async def notify_admin_requeued_processing(
    *,
    bot: Bot,
    settings: Settings,
    provider: str,
    rows: list[dict[str, Any]],
    older_than_sec: int,
) -> None:
    if not rows:
        return
    lines = [
        "⚠️ Платежи возвращены из processing в pending",
        f"Провайдер: {provider}",
        f"Порог: {older_than_sec} сек",
        f"Количество: {len(rows)}",
    ]
    preview = rows[:8]
    for row in preview:
        external_id = str(row.get("external_id") or "")
        tg_id = int(row.get("telegram_id") or 0)
        lines.append(f"- {external_id} (tg:{tg_id})")
    if len(rows) > len(preview):
        lines.append(f"...и еще {len(rows) - len(preview)}")
    text = "\n".join(lines)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(int(admin_id), text)
        except Exception:
            logging.exception("Requeue notify: failed to send to admin %s", admin_id)


async def notify_admin_worker_alert(
    *,
    bot: Bot,
    settings: Settings,
    key: str,
    title: str,
    details: str = "",
) -> None:
    if not settings.admin_alerts_enabled:
        return

    now = time.time()
    cooldown = max(0, settings.admin_alert_cooldown_sec)
    if cooldown > 0:
        async with _worker_alert_lock:
            last_sent = _worker_alert_last_sent.get(key, 0.0)
            if now - last_sent < cooldown:
                return
            _worker_alert_last_sent[key] = now

    details_text = details.strip()
    if len(details_text) > WORKER_ALERT_PREVIEW_LIMIT:
        details_text = details_text[:WORKER_ALERT_PREVIEW_LIMIT].rstrip() + "..."

    lines = [
        f"🚨 {title}",
        f"Ключ: {key}",
        f"Время: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M:%S')} UTC",
    ]
    if details_text:
        lines.append(f"Детали: {details_text}")
    text = "\n".join(lines)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(int(admin_id), text)
        except Exception:
            logging.exception("Worker alert: failed to send to admin %s", admin_id)


async def send_daily_report(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
) -> None:
    try:
        ops_text = await asyncio.wait_for(
            build_ops_report_text(settings, marzban, sar_seconds=60),
            timeout=75,
        )
    except Exception:
        logging.exception("Daily report: ops text failed")
        ops_text = "Ops отчет: ошибка формирования"
    try:
        payments_text = await asyncio.wait_for(build_payments_summary(repo), timeout=5)
    except Exception:
        logging.exception("Daily report: payments text failed")
        payments_text = "Платежи: ошибка формирования"
    try:
        stats_text = await asyncio.wait_for(build_admin_stats_text(repo, marzban), timeout=20)
    except Exception:
        logging.exception("Daily report: stats text failed")
        stats_text = "Статистика: ошибка формирования"
    header = f"📅 Ежедневный отчет ({datetime.now().strftime('%d.%m.%Y %H:%M')})"
    full = f"{header}\n\n{ops_text}\n\n{payments_text}\n\n{stats_text}"
    for chunk in split_message(full, limit=3500):
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(int(admin_id), chunk)
            except Exception:
                logging.exception("Daily report: failed to send to admin %s", admin_id)


async def daily_ops_report_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.ops_report_enabled:
        return
    last_sent: datetime.date | None = None
    while not stop_event.is_set():
        now = datetime.now()
        target = now.replace(
            hour=settings.ops_report_hour,
            minute=settings.ops_report_minute,
            second=0,
            microsecond=0,
        )
        if now >= target and (last_sent is None or last_sent != now.date()):
            try:
                await send_daily_report(settings=settings, repo=repo, marzban=marzban, bot=bot)
                last_sent = now.date()
            except Exception:
                logging.exception("Daily report: failed")
            # wait until next day target
            target = target + timedelta(days=1)
        elif now >= target:
            target = target + timedelta(days=1)
        wait_seconds = max(30, int((target - now).total_seconds()))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            continue


async def deploy_report_worker(
    *,
    settings: Settings,
    repo: Repo,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    interval = 15
    while not stop_event.is_set():
        try:
            await send_deploy_report_if_any(bot, settings, repo)
        except Exception:
            logging.exception("Deploy report worker failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def cryptobot_auto_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    await _cryptobot_auto_worker(
        settings=settings,
        repo=repo,
        marzban=marzban,
        bot=bot,
        stop_event=stop_event,
        notify_admin_requeued_processing_fn=notify_admin_requeued_processing,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
        cryptobot_check_invoice_fn=cryptobot_check_invoice,
        apply_paid_payment_fn=apply_paid_payment,
        notify_access_updated_fn=notify_access_updated,
    )


def auto_renew_plan(settings: Settings) -> Plan:
    return _auto_renew_plan(settings, find_plan_fn=find_plan)


def auto_renew_provider(settings: Settings) -> str | None:
    return _auto_renew_provider(settings)


async def yookassa_auto_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    await _yookassa_auto_worker(
        settings=settings,
        repo=repo,
        marzban=marzban,
        bot=bot,
        stop_event=stop_event,
        notify_admin_requeued_processing_fn=notify_admin_requeued_processing,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
        yookassa_check_payment_fn=yookassa_check_payment,
        apply_paid_payment_fn=apply_paid_payment,
        notify_access_updated_fn=notify_access_updated,
    )


async def subscription_renewal_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    await _subscription_renewal_worker(
        settings=settings,
        repo=repo,
        marzban=marzban,
        bot=bot,
        stop_event=stop_event,
        auto_renew_provider_fn=auto_renew_provider,
        auto_renew_plan_fn=auto_renew_plan,
        device_label_fn=_device_label,
        format_expire_fn=format_expire,
        format_time_left_fn=format_time_left,
        renewal_actions_keyboard_fn=renewal_actions_keyboard,
        plan_title_fn=plan_title,
        yookassa_create_payment_fn=yookassa_create_payment,
        cryptobot_create_invoice_fn=cryptobot_create_invoice,
        pay_action_keyboard_fn=pay_action_keyboard,
        notify_admin_worker_alert_fn=notify_admin_worker_alert,
    )

def build_router(settings: Settings, repo: Repo, marzban: MarzbanClient) -> Router:
    router = Router()
    message_limiter = InMemoryRateLimiter(
        limit=settings.user_rate_limit_count,
        window_sec=settings.user_rate_limit_window_sec,
    )
    callback_limiter = InMemoryRateLimiter(
        limit=settings.callback_rate_limit_count,
        window_sec=settings.callback_rate_limit_window_sec,
    )
    bot_username_cache: str | None = None
    pending_device_rename: dict[int, int] = {}
    pending_device_add_prompt: set[int] = set()
    pending_issue: set[int] = set()
    pending_user_lookup: set[int] = set()
    pending_broadcast_prompt: set[int] = set()
    pending_broadcast_text: dict[int, str] = {}
    pending_broadcast_format: dict[int, str] = {}
    pending_broadcast_buttons: dict[int, bool] = {}

    async def track_event(
        event_type: str,
        *,
        telegram_id: int | None = None,
        event_value: str = "",
        event_meta: dict[str, Any] | None = None,
    ) -> None:
        try:
            await repo.log_event(
                event_type=event_type,
                telegram_id=telegram_id,
                event_value=event_value,
                event_meta=event_meta,
            )
        except Exception:
            logging.exception("Failed to track event %s", event_type)

    def start_deploy(script: Path) -> bool:
        unit_name = f"vpn-ops-deploy-{int(time.time())}"
        try:
            result = subprocess.run(
                ["systemd-run", "--unit", unit_name, "--collect", str(script)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logging.info("Deploy started via systemd-run unit %s", unit_name)
                return True
            logging.warning(
                "systemd-run failed (rc=%s): %s",
                result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
        except Exception:
            logging.exception("Deploy start via systemd-run failed")
        return False

    async def schedule_deploy_report(bot: Bot) -> None:
        await asyncio.sleep(6)
        await send_deploy_report_if_any(bot, settings, repo)

    async def get_bot_username(bot: Bot) -> str:
        nonlocal bot_username_cache
        if bot_username_cache:
            return bot_username_cache
        me = await bot.get_me()
        bot_username_cache = str(me.username or "").strip()
        return bot_username_cache

    user_lookup_ctx = UserLookupContext(
        repo=repo,
        marzban=marzban,
        build_username=build_username,
        format_expire=format_expire,
        format_limit=format_limit,
        format_used=format_used,
        format_last_online=format_last_online,
        device_label=_device_label,
    )

    broadcast_preview_ctx = BroadcastPreviewContext(
        repo=repo,
        pending_broadcast_format=pending_broadcast_format,
        pending_broadcast_buttons=pending_broadcast_buttons,
        broadcast_format_label=broadcast_format_label,
        broadcast_parse_mode=broadcast_parse_mode,
        broadcast_confirm_keyboard=broadcast_confirm_keyboard,
    )

    async def send_user_lookup(message: Message, target_id: int) -> None:
        await send_user_lookup_impl(
            message=message,
            target_id=target_id,
            ctx=user_lookup_ctx,
        )

    async def send_broadcast_preview(message: Message, body: str, *, admin_id: int | None = None) -> None:
        await send_broadcast_preview_impl(
            message=message,
            body=body,
            admin_id=admin_id,
            ctx=broadcast_preview_ctx,
        )

    async def replace_device_slot(
        *,
        telegram_id: int,
        slot: int,
    ) -> tuple[str, str, dict[str, Any]]:
        row = await repo.get_device(telegram_id, slot)
        if not row:
            raise RuntimeError("Устройство не найдено в локальной БД.")
        old_username = str(row.get("marzban_username") or "").strip()
        if not old_username:
            raise RuntimeError("Для устройства не найден marzban_username.")
        old_user = await marzban.get_user(old_username)
        if not old_user:
            raise RuntimeError(f"Старый профиль {old_username} не найден в Marzban.")

        new_username = build_replacement_username(telegram_id, slot)
        new_user = await marzban.create_user(
            username=new_username,
            expire=int(old_user.get("expire", 0) or 0),
            data_limit=int(old_user.get("data_limit", 0) or 0),
        )

        await repo.upsert_device(
            telegram_id,
            slot,
            new_username,
            row.get("device_name"),
        )
        if slot == 1:
            await repo.upsert_user(telegram_id, new_username)

        try:
            await marzban.modify_user(old_username, {"status": "disabled"})
        except Exception:
            logging.exception("device_replace: failed to disable old username %s", old_username)

        return old_username, new_username, new_user

    async def list_replaceable_devices(telegram_id: int) -> list[dict[str, Any]]:
        devices = await repo.list_devices(telegram_id)
        result: list[dict[str, Any]] = []
        for row in devices:
            username = str(row.get("marzban_username") or "").strip()
            if not username:
                continue
            user = await marzban.get_user(username)
            if not user:
                continue
            status = str(user.get("status", "unknown"))
            if status != "active":
                continue
            result.append(row)
        return result

    async def guard_message_rate_limit(message: Message) -> bool:
        if not message.from_user:
            return False
        tg_id = int(message.from_user.id)
        try:
            await repo.touch_chat(tg_id)
        except Exception:
            logging.exception("Failed to touch chat %s on message", tg_id)
        if is_admin(tg_id, settings):
            return True
        if message_limiter.allow(f"msg:{tg_id}"):
            return True
        await message.answer("Слишком много запросов. Подождите 10-20 секунд и повторите.")
        return False

    async def guard_callback_rate_limit(callback: CallbackQuery) -> bool:
        if not callback.from_user:
            return False
        tg_id = int(callback.from_user.id)
        try:
            await repo.touch_chat(tg_id)
        except Exception:
            logging.exception("Failed to touch chat %s on callback", tg_id)
        if is_admin(tg_id, settings):
            return True
        if callback_limiter.allow(f"cb:{tg_id}"):
            return True
        await callback.answer("Слишком часто. Подождите немного.", show_alert=True)
        return False

    async def handle_grant_perm(message: Message) -> bool:
        if not message.text:
            return False
        raw = message.text
        if "/grant_perm" not in raw:
            return False
        parts = raw.split()
        cmd_index = None
        for i, part in enumerate(parts):
            if part.startswith("/grant_perm"):
                cmd_index = i
                break
        if cmd_index is None:
            return False
        cmd = parts[cmd_index].split("@", 1)[0]
        if cmd != "/grant_perm":
            return False
        if not await guard_message_rate_limit(message):
            return True
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return True
        args = parts[cmd_index + 1 :]
        if len(args) not in {1, 2}:
            await message.answer("Использование: /grant_perm <telegram_id> [gb]")
            return True
        try:
            target = int(args[0])
            gb = int(args[1]) if len(args) == 2 else 0
        except ValueError:
            await message.answer("Ошибка формата. Пример: /grant_perm 386029735 0")
            return True
        if gb < 0:
            await message.answer("GB должно быть >= 0.")
            return True
        updated = await extend_access_all_devices(
            telegram_id=target,
            days=0,
            gb=gb,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        expire_val = None
        try:
            primary_row = await repo.get_user(target)
            primary_username = (
                str(primary_row["marzban_username"])
                if primary_row
                else build_username(target)
            )
            primary_user = await marzban.get_user(primary_username)
            expire_val = primary_user.get("expire") if primary_user else None
        except Exception:
            logging.exception("grant_perm: failed to read expire after perm grant for %s", target)
        logging.info("grant_perm: perm access for %s, expire=%s", target, expire_val)
        await message.answer("Готово. Бессрочный доступ выдан.")
        await notify_access_updated(
            message.bot,
            target,
            updated,
            "Вам выдан бессрочный доступ.",
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        return True


    register_user_message_handlers(
        router=router,
        deps=UserMessageDeps(
            settings=settings,
            repo=repo,
            guard_message_rate_limit=guard_message_rate_limit,
            extract_start_payload=extract_start_payload,
            parse_referrer_from_payload=parse_referrer_from_payload,
            build_start_text=build_start_text,
            plan_gb_text=plan_gb_text,
            format_device_limit=format_device_limit,
            keyboard_for_user=keyboard_for_user,
            is_admin_fn=is_admin,
            track_event=track_event,
            enabled_payment_providers=enabled_payment_providers,
            get_bot_username=get_bot_username,
            build_user_faq_text=build_user_faq_text,
            quick_connect_guide_text=quick_connect_guide_text,
            normalize_channel_url=normalize_channel_url,
        ),
    )
    @router.message(F.text.contains("/grant_perm"))
    async def grant_perm_any(message: Message) -> None:
        if await handle_grant_perm(message):
            return








    @router.message(Command("admin"))
    async def admin_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        await message.answer(
            "Админ-кабинет:\n"
            "- Статистика по пользователям и платежам\n"
            "- Быстрые действия без ручного ввода команд",
            reply_markup=admin_panel_keyboard(),
        )

    @router.message(Command("broadcast"))
    async def broadcast_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        text = (message.text or "").split(maxsplit=1)
        if len(text) < 2 or not text[1].strip():
            pending_broadcast_prompt.add(int(message.from_user.id))
            pending_broadcast_format.setdefault(int(message.from_user.id), "plain")
            pending_broadcast_buttons.setdefault(int(message.from_user.id), True)
            await message.answer("Введите текст рассылки или «отмена».")
            return
        body = text[1].strip()
        admin_id = int(message.from_user.id)
        pending_broadcast_text[admin_id] = body
        pending_broadcast_format.setdefault(admin_id, "plain")
        pending_broadcast_buttons.setdefault(admin_id, True)
        await send_broadcast_preview(message, body)

    @router.message(Command("user"))
    async def user_lookup_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Использование: /user <telegram_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("ID должен быть числом. Пример: /user 386029735")
            return
        await send_user_lookup(message, target_id)

    @router.message(Command("config"))
    async def config_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        _, user, _ = await ensure_device(
            telegram_id=int(message.from_user.id),
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=False,
        )
        if not user:
            await message.answer("❗ Профиль не найден. Нажмите «🔑 Получить подписку».")
            return
        await send_status(message, user)
        await send_device_links(
            message=message,
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(Command("diag"))
    async def diag_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        lines: list[str] = [f"🧪 Диагностика\nTG: {tg_id}"]

        devices = await repo.list_devices(tg_id)
        if not devices:
            lines.append("Профиль не найден. Нажмите «🔑 Получить подписку».")
            await message.answer("\n".join(lines))
            return

        lines.append("Устройства:")
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            username = str(row.get("marzban_username") or "").strip()
            mz_user = await marzban.get_user(username) if username else None
            if not mz_user:
                lines.append(f"- {device_id}. {label}: не найдено в Marzban")
                continue
            status = str(mz_user.get("status", "unknown"))
            used = format_used(int(mz_user.get("used_traffic", 0) or 0))
            expire = format_expire(int(mz_user.get("expire", 0) or 0))
            online = format_last_online(
                mz_user.get("online_at") or mz_user.get("last_online") or mz_user.get("last_online_at")
            )
            lines.append(
                f"- {device_id}. {label}: {status}, онлайн: {online}, трафик: {used}, до: {expire}"
            )

        latest_payment = await repo.get_latest_payment(tg_id)
        if latest_payment:
            purpose = str(latest_payment.get("purpose") or "plan")
            provider = str(latest_payment.get("provider") or "")
            status = str(latest_payment.get("status") or "")
            amount = float(latest_payment.get("amount_rub") or 0)
            updated = int(latest_payment.get("updated_at") or 0)
            updated_text = (
                datetime.fromtimestamp(updated, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
                if updated > 0
                else "n/a"
            )
            lines.append(
                f"Последний платеж: {provider}, {purpose}, {amount:.2f} RUB, {status}, {updated_text}"
            )
        else:
            lines.append("Платежи: не найдено")

        lines.append("Если есть проблемы, отправьте «⚠️ Проблема с подключением».")
        await message.answer("\n".join(lines))

    @router.message(Command("buy"))
    async def buy_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            await message.answer(
                "💳 Выберите тариф для продления устройства 1:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=1),
            )
            return
        tg_id = int(message.from_user.id)
        devices = await repo.list_devices(tg_id)
        if not devices:
            await message.answer(
                "💳 Выберите тариф для продления основного ключа (устройство 1):\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=1),
            )
            return
        if len(devices) == 1:
            only_slot = int(devices[0]["device_id"])
            await message.answer(
                f"💳 Выберите тариф для продления устройства {only_slot}:\n"
                + plans_list_text(settings),
                reply_markup=buy_plan_keyboard(settings, target="slot", device_id=only_slot),
            )
            return
        await message.answer(
            "💳 Выберите, что продлить:",
            reply_markup=buy_target_keyboard(devices),
        )

    @router.message(Command("device"))
    async def device_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        row = await repo.get_user(tg_id)
        if not row:
            await message.answer("❗ Сначала получите ссылку подписки.")
            return
        devices = await repo.list_devices(tg_id)
        if settings.device_limit > 0 and len(devices) >= settings.device_limit:
            await message.answer("Лимит устройств уже исчерпан.")
            return
        if not await repo.has_paid_plan_payment(tg_id):
            await message.answer(
                "📱 Доп. устройство доступно только после оплаты основного тарифа.\n"
                "Сначала нажмите «Купить доступ»."
            )
            return
        await message.answer(
            f"📱 Доп. устройство: {settings.device_add_rub:.2f} RUB.\n"
            "Оплата добавляет только новый слот устройства.\n"
            "Срок доступа не продлевается.\n"
            "После оплаты устройство появится автоматически.\n"
            "Название можно задать через «Переименовать устройство».",
            reply_markup=device_methods_keyboard(settings),
        )

    @router.message(Command("replace"))
    async def replace_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        devices = await list_replaceable_devices(tg_id)
        if not devices:
            await message.answer("Активные устройства не найдены. Сначала получите подписку.")
            return
        kb = _devices_replace_keyboard(devices)
        await message.answer(
            "Выберите устройство для перевыпуска ссылки.\n"
            "Старая ссылка выбранного устройства будет отключена.",
            reply_markup=kb,
        )

    @router.message(Command("devices"))
    async def devices_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        devices = await repo.list_devices(int(message.from_user.id))
        if not devices:
            await message.answer("Устройства не найдены. Сначала получите подписку.")
            return
        lines: list[str] = []
        for row in devices:
            device_id = int(row["device_id"])
            label = _device_label(device_id, row.get("device_name"))
            if label.startswith("Устройство"):
                lines.append(f"{device_id}. {label}")
            else:
                lines.append(f"{device_id}. Устройство {device_id} — {label}")
        await message.answer("Ваши устройства:\n" + "\n".join(lines))

    @router.message(Command("device_name"))
    async def device_name_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Использование: /device_name <id> <имя устройства>")
            return
        try:
            device_id = int(parts[1])
        except ValueError:
            await message.answer("ID устройства должен быть числом. Пример: /device_name 2 Мой ноутбук")
            return
        if device_id < 1:
            await message.answer("ID устройства должен быть >= 1")
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await message.answer(f"ID устройства должен быть в диапазоне 1..{settings.device_limit}")
            return
        name = normalize_device_name(parts[2])
        if not name:
            await message.answer("Имя устройства не может быть пустым.")
            return
        row = await repo.get_device(int(message.from_user.id), device_id)
        if not row:
            await message.answer("Устройство не найдено. Сначала получите подписку.")
            return
        await repo.set_device_name(int(message.from_user.id), device_id, name)
        await message.answer(f"✅ Устройство {device_id} теперь называется: {name}")

    @router.message(Command("check"))
    async def check_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            providers = enabled_payment_providers(settings)
            await message.answer(
                "Использование: /check <" + "|".join(providers) + "> <payment_id>"
                if providers
                else "Провайдеры оплаты не настроены."
            )
            return
        provider = parts[1].lower().strip()
        allowed = enabled_payment_providers(settings)
        if provider not in set(allowed):
            if not allowed:
                await message.answer("Провайдеры оплаты не настроены.")
            else:
                await message.answer("Допустимые провайдеры: " + ", ".join(allowed))
            return
        result, updated = await check_and_apply_payment(
            provider=provider,
            external_id=parts[2],
            telegram_id=int(message.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
            bot=message.bot,
        )
        await message.answer(result)
        if updated:
            await send_status(message, updated)
            await send_device_links(
                message=message,
                telegram_id=int(message.from_user.id),
                repo=repo,
                marzban=marzban,
                settings=settings,
            )

    @router.message(F.text.in_({"🔑 Получить конфиг", "🔑 Получить подписку"}))
    async def get_config(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        await track_event("config_requested", telegram_id=tg_id)
        _, user, created = await ensure_device(
            telegram_id=tg_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=True,
        )
        if created:
            await message.answer(
                f"🎁 Тестовый доступ выдан: {settings.trial_days} день, {plan_gb_text(settings.trial_gb)}."
            )
            await track_event("trial_issued", telegram_id=tg_id)
        await send_status(message, user or {})
        await send_device_links(
            message=message,
            telegram_id=tg_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(F.text == "📊 Мой статус")
    async def status_cmd(message: Message) -> None:
        await config_cmd(message)

    @router.message(F.text == "💳 Купить доступ")
    async def buy_btn(message: Message) -> None:
        await buy_cmd(message)

    @router.message(F.text == "📂 Еще")
    async def more_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(
            "<b>Дополнительные действия</b>\n"
            "Выберите нужный пункт:",
            reply_markup=more_actions_keyboard(),
            parse_mode="HTML",
        )

    @router.message(F.text == "📱 Добавить устройство")
    async def device_btn(message: Message) -> None:
        await device_cmd(message)

    @router.message(F.text == "🔁 Заменить устройство")
    async def replace_btn(message: Message) -> None:
        await replace_cmd(message)

    @router.message(F.text == "✏️ Переименовать устройство")
    async def device_rename_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        devices = await repo.list_devices(int(message.from_user.id))
        if not devices:
            await message.answer("Устройства не найдены. Сначала получите подписку.")
            return
        kb = _devices_rename_keyboard(devices)
        await message.answer("Выберите устройство для переименования:", reply_markup=kb)

    @router.message(F.text == "🎁 Рефералка")
    async def ref_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        username = await get_bot_username(message.bot)
        if not username:
            await message.answer("Не удалось получить username бота. Попробуйте позже.")
            return
        link = f"https://t.me/{username}?start=ref_{tg_id}"
        stats = await repo.get_referral_stats(tg_id)
        await message.answer(
            "🎁 Реферальная программа:\n"
            f"- Бонус за оплаченного друга: +{max(0, settings.referral_bonus_days)} дн.\n"
            f"- Приглашено: {stats['total']}\n"
            f"- Бонус выдан: {stats['rewarded']}\n"
            f"- Ожидают первую оплату: {stats['pending']}\n\n"
            "Ваша ссылка:\n"
            f"{link}"
        )

    @router.message(F.text == "❓ FAQ")
    async def faq_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(build_user_faq_text(), parse_mode="HTML")

    @router.message(F.text == "🆘 Поддержка")
    async def support_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            await track_event("support_opened", telegram_id=tg_id)
        safe_support_text = html.escape(settings.support_text)
        if settings.support_username:
            await message.answer(
                "<b>🆘 Поддержка</b>\n"
                f"{safe_support_text}\n\n"
                f"Контакт: https://t.me/{settings.support_username}",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "<b>🆘 Поддержка</b>\n"
                f"{safe_support_text}\n\n"
                "Контакт поддержки пока не задан администратором.",
                parse_mode="HTML",
            )

    @router.message(F.text == "📢 Наш канал")
    async def channel_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            await track_event("channel_opened", telegram_id=tg_id)
        link = normalize_channel_url(settings.channel_url)
        if link:
            await message.answer(f"<b>📢 Наш канал</b>\n{link}", parse_mode="HTML")
            return
        await message.answer("Канал пока не настроен. Администратор скоро добавит ссылку.")

    @router.message(F.text == "⚠️ Проблема с подключением")
    async def issue_btn(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        pending_issue.add(tg_id)
        await message.answer(
            "Опишите проблему одним сообщением по шаблону:\n"
            "1) Время (дата и время по МСК)\n"
            "2) Устройство и приложение (iOS/Android/Windows + клиент)\n"
            "3) Что именно не работает\n"
            "4) Ошибка/скрин (если есть)\n"
            "5) Пробовали переимпорт/перезапуск\n\n"
            "Напишите «отмена» чтобы выйти."
        )

    @router.message(F.text == "🛠 Админ-кабинет")
    async def admin_btn(message: Message) -> None:
        await admin_cmd(message)

    register_user_callback_handlers(
        router=router,
        deps=UserCallbackDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_callback_rate_limit=guard_callback_rate_limit,
            list_replaceable_devices=list_replaceable_devices,
            get_bot_username=get_bot_username,
            build_user_faq_text=build_user_faq_text,
            normalize_channel_url=normalize_channel_url,
            pending_issue=pending_issue,
            pending_device_rename=pending_device_rename,
            replace_device_slot=replace_device_slot,
            send_status=send_status,
            send_device_links=send_device_links,
            collect_device_links=collect_device_links,
            send_configs_in_chat=send_configs_in_chat,
            render_config_block=_render_config_block,
            plans_list_text=plans_list_text,
            buy_plan_keyboard=buy_plan_keyboard,
            find_plan=find_plan,
            plan_title=plan_title,
            plan_gb_text=plan_gb_text,
            payment_methods_keyboard=payment_methods_keyboard,
            cryptobot_create_invoice=cryptobot_create_invoice,
            yookassa_create_payment=yookassa_create_payment,
            track_event=track_event,
            pay_action_keyboard=pay_action_keyboard,
            next_device_slot=next_device_slot,
            check_and_apply_payment=check_and_apply_payment,
            device_methods_keyboard=device_methods_keyboard,
            devices_replace_keyboard=_devices_replace_keyboard,
            devices_rename_keyboard=_devices_rename_keyboard,
            device_replace_confirm_keyboard=_device_replace_confirm_keyboard,
            device_label=_device_label,
        ),
    )

    register_fallback_handler(
        router=router,
        deps=FallbackDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_message_rate_limit=guard_message_rate_limit,
            pending_user_lookup=pending_user_lookup,
            pending_device_add_prompt=pending_device_add_prompt,
            pending_broadcast_prompt=pending_broadcast_prompt,
            pending_broadcast_text=pending_broadcast_text,
            pending_broadcast_format=pending_broadcast_format,
            pending_broadcast_buttons=pending_broadcast_buttons,
            pending_device_rename=pending_device_rename,
            pending_issue=pending_issue,
            send_user_lookup=send_user_lookup,
            ensure_device=ensure_device,
            send_broadcast_preview=send_broadcast_preview,
            normalize_device_name=normalize_device_name,
            track_event=track_event,
            keyboard_for_user=keyboard_for_user,
            is_admin_fn=is_admin,
        ),
    )

    register_admin_callback_handlers(
        router=router,
        deps=AdminCallbackDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_callback_rate_limit=guard_callback_rate_limit,
            is_admin_fn=is_admin,
            admin_panel_keyboard=admin_panel_keyboard,
            admin_plans_text=admin_plans_text,
            admin_plans_keyboard=admin_plans_keyboard,
            preset_plans=_preset_plans,
            plans_to_json=_plans_to_json,
            update_env_file=update_env_file,
            plan_title=plan_title,
            plan_gb_text=plan_gb_text,
            build_admin_stats_text=build_admin_stats_text,
            build_ops_report_text=build_ops_report_text,
            start_deploy=start_deploy,
            schedule_deploy_report=schedule_deploy_report,
            pending_user_lookup=pending_user_lookup,
            pending_device_add_prompt=pending_device_add_prompt,
            pending_broadcast_prompt=pending_broadcast_prompt,
            pending_broadcast_format=pending_broadcast_format,
            pending_broadcast_buttons=pending_broadcast_buttons,
            pending_broadcast_text=pending_broadcast_text,
            broadcast_next_format=broadcast_next_format,
            send_broadcast_preview=send_broadcast_preview,
            broadcast_parse_mode=broadcast_parse_mode,
            keyboard_for_user=keyboard_for_user,
            build_ref_top_text=build_ref_top_text,
            enabled_payment_providers=enabled_payment_providers,
            build_support_templates_text=build_support_templates_text,
        ),
    )

    register_admin_message_handlers(
        router=router,
        deps=AdminMessageDeps(
            settings=settings,
            repo=repo,
            marzban=marzban,
            guard_message_rate_limit=guard_message_rate_limit,
            is_admin_fn=is_admin,
            extend_access_all_devices=extend_access_all_devices,
            build_username=build_username,
            notify_access_updated=notify_access_updated,
            extend_access=extend_access,
            ensure_device=ensure_device,
            extend_access_device=extend_access_device,
            send_status_to_bot=send_status_to_bot,
            send_device_links_to_bot=send_device_links_to_bot,
            sync_expire_across_devices=sync_expire_across_devices,
            format_expire=format_expire,
            replace_device_slot=replace_device_slot,
            env_editable_keys=ENV_EDITABLE_KEYS,
            coerce_env_value=coerce_env_value,
            update_env_file=update_env_file,
            start_deploy=start_deploy,
            schedule_deploy_report=schedule_deploy_report,
            broadcast_menu_update=broadcast_menu_update,
            build_admin_stats_text=build_admin_stats_text,
            build_ref_top_text=build_ref_top_text,
            build_ops_report_text=build_ops_report_text,
            extend_access_days_only=extend_access_days_only,
        ),
    )

    return router


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    settings = Settings.load()
    logging.info(
        "Runtime network settings: iface=%s, port_speed_mbps=%.0f",
        settings.net_iface,
        settings.port_speed_mbps,
    )
    repo = Repo(settings.db_path)
    await repo.open()
    marzban = MarzbanClient(settings)
    bot = Bot(token=settings.bot_token)
    await send_deploy_report_if_any(bot, settings, repo)
    dp = Dispatcher()
    dp.include_router(build_router(settings, repo, marzban))
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        cryptobot_auto_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    yookassa_task = asyncio.create_task(
        yookassa_auto_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    report_task = asyncio.create_task(
        daily_ops_report_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )
    deploy_report_task = asyncio.create_task(
        deploy_report_worker(
            settings=settings,
            repo=repo,
            bot=bot,
            stop_event=stop_event,
        )
    )
    renewal_task = asyncio.create_task(
        subscription_renewal_worker(
            settings=settings,
            repo=repo,
            marzban=marzban,
            bot=bot,
            stop_event=stop_event,
        )
    )

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        worker_task.cancel()
        yookassa_task.cancel()
        report_task.cancel()
        deploy_report_task.cancel()
        renewal_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        try:
            await yookassa_task
        except asyncio.CancelledError:
            pass
        try:
            await report_task
        except asyncio.CancelledError:
            pass
        try:
            await deploy_report_task
        except asyncio.CancelledError:
            pass
        try:
            await renewal_task
        except asyncio.CancelledError:
            pass
        await marzban.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())








