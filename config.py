from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.vpnbot.bot_formatters import plan_title
from models import Plan


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
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(_detect_port_speed_mbps_async(iface))
        except Exception:
            return None
    return None


async def _detect_port_speed_mbps_async(iface: str) -> float | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ethtool",
            iface,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return None
    except Exception:
        return None

    out = (stdout or b"").decode("utf-8", errors="ignore")
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


def _validate_required_env() -> None:
    if not os.getenv("BOT_TOKEN", "").strip():
        raise ValueError("BOT_TOKEN is required")
    if not os.getenv("BOT_ADMIN_IDS", "").strip():
        raise ValueError("BOT_ADMIN_IDS is required")
    if not os.getenv("MARZBAN_BASE_URL", "").strip():
        raise ValueError("MARZBAN_BASE_URL is required")
    if not os.getenv("MARZBAN_USERNAME", "").strip() or not os.getenv("MARZBAN_PASSWORD", "").strip():
        raise ValueError("MARZBAN_USERNAME and MARZBAN_PASSWORD are required")


def _load_required_block() -> dict[str, Any]:
    return {
        "bot_token": os.getenv("BOT_TOKEN", "").strip(),
        "admin_ids": parse_admin_ids(os.getenv("BOT_ADMIN_IDS", "").strip()),
    }


def _load_marzban_block() -> dict[str, Any]:
    return {
        "marzban_base_url": os.getenv("MARZBAN_BASE_URL", "").strip().rstrip("/"),
        "marzban_username": os.getenv("MARZBAN_USERNAME", "").strip(),
        "marzban_password": os.getenv("MARZBAN_PASSWORD", "").strip(),
        "marzban_verify_ssl": env_bool("MARZBAN_VERIFY_SSL", True),
        "marzban_proxy_protocol": os.getenv("MARZBAN_PROXY_PROTOCOL", "vless").strip().lower(),
        "config_delivery_mode": normalize_config_delivery_mode(
            os.getenv("CONFIG_DELIVERY_MODE", "direct")
        ),
        "subscription_public_base_url": normalize_public_base_url(
            os.getenv("SUBSCRIPTION_PUBLIC_BASE_URL", "")
        ),
    }


def _load_network_block() -> dict[str, Any]:
    iface = _resolve_net_iface(os.getenv("NET_IFACE", "").strip())
    return {
        "net_iface": iface,
        "port_speed_mbps": _resolve_port_speed_mbps(
            os.getenv("PORT_SPEED_Mbps", "auto").strip(),
            iface,
        ),
        "port_utilization": float(os.getenv("PORT_UTILIZATION", "0.8")),
        "concurrency_ratio": float(os.getenv("CONCURRENCY_RATIO", "0.05")),
    }


def _load_trial_block() -> dict[str, Any]:
    return {
        "trial_days": int(os.getenv("TRIAL_DAYS", "1")),
        "trial_gb": int(os.getenv("TRIAL_GB", "0")),
    }


def _load_plans_block() -> dict[str, Any]:
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
    base = plans[0]
    return {
        "plans": plans,
        "pay_days": base.days,
        "pay_gb": base.gb,
        "pay_rub": base.rub,
    }


def _load_cryptobot_block() -> dict[str, Any]:
    return {
        "cryptobot_token": os.getenv("CRYPTOBOT_TOKEN", "").strip(),
        "cryptobot_testnet": env_bool("CRYPTOBOT_TESTNET", False),
        "cryptobot_fiat": os.getenv("CRYPTOBOT_FIAT", "RUB").strip().upper(),
        "cryptobot_accepted_assets": os.getenv("CRYPTOBOT_ACCEPTED_ASSETS", "USDT,TON").strip(),
        "cryptobot_expires_in": int(os.getenv("CRYPTOBOT_EXPIRES_IN", "3600")),
        "cryptobot_poll_seconds": int(os.getenv("CRYPTOBOT_POLL_SECONDS", "45")),
    }


def _load_yookassa_block() -> dict[str, Any]:
    return {
        "yookassa_shop_id": os.getenv("YOOKASSA_SHOP_ID", "").strip(),
        "yookassa_secret_key": os.getenv("YOOKASSA_SECRET_KEY", "").strip(),
        "yookassa_return_url": os.getenv("YOOKASSA_RETURN_URL", "https://t.me").strip(),
        "yookassa_poll_seconds": int(os.getenv("YOOKASSA_POLL_SECONDS", "60")),
        "payment_processing_requeue_seconds": max(
            60, int(os.getenv("PAYMENT_PROCESSING_REQUEUE_SECONDS", "600"))
        ),
    }


def _load_rate_limit_block() -> dict[str, Any]:
    return {
        "user_rate_limit_count": int(os.getenv("USER_RATE_LIMIT_COUNT", "12")),
        "user_rate_limit_window_sec": int(os.getenv("USER_RATE_LIMIT_WINDOW_SEC", "30")),
        "callback_rate_limit_count": int(os.getenv("CALLBACK_RATE_LIMIT_COUNT", "20")),
        "callback_rate_limit_window_sec": int(os.getenv("CALLBACK_RATE_LIMIT_WINDOW_SEC", "30")),
    }


def _load_misc_block() -> dict[str, Any]:
    return {
        "support_username": os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
        "support_text": os.getenv(
            "SUPPORT_TEXT", "Напишите нам, поможем с подключением и оплатой."
        ).strip(),
        "channel_url": os.getenv("CHANNEL_URL", "").strip(),
        "referral_bonus_days": int(os.getenv("REFERRAL_BONUS_DAYS", "3")),
        "device_limit": int(os.getenv("DEVICE_LIMIT", "1")),
        "device_add_rub": float(os.getenv("DEVICE_ADD_RUB", "99")),
        "deploy_broadcast_users": env_bool("DEPLOY_BROADCAST_USERS", False),
        "db_path": os.getenv("DB_PATH", "./data/bot.sqlite3").strip(),
    }


def _load_ops_report_block() -> dict[str, Any]:
    return {
        "ops_report_enabled": env_bool("OPS_REPORT_ENABLED", True),
        "ops_report_hour": max(0, min(23, int(os.getenv("OPS_REPORT_HOUR", "9")))),
        "ops_report_minute": max(0, min(59, int(os.getenv("OPS_REPORT_MINUTE", "0")))),
    }


def _load_renewal_block() -> dict[str, Any]:
    return {
        "renewal_alerts_enabled": env_bool("RENEWAL_ALERTS_ENABLED", True),
        "renewal_alert_interval_sec": max(
            60, int(os.getenv("RENEWAL_ALERT_INTERVAL_SEC", "300"))
        ),
        "renewal_reminder_hours": parse_int_csv(
            os.getenv("RENEWAL_REMINDER_HOURS", "72,24,6"),
            default=(6, 24, 72),
        ),
        "renewal_expired_alert_enabled": env_bool("RENEWAL_EXPIRED_ALERT_ENABLED", True),
    }


def _load_admin_alerts_block() -> dict[str, Any]:
    return {
        "admin_alerts_enabled": env_bool("ADMIN_ALERTS_ENABLED", True),
        "admin_alert_cooldown_sec": max(
            0, int(os.getenv("ADMIN_ALERT_COOLDOWN_SEC", "900"))
        ),
    }


def _load_auto_renew_block() -> dict[str, Any]:
    auto_provider = os.getenv("AUTO_RENEW_INVOICE_PROVIDER", "card").strip().lower()
    if auto_provider not in {"card", "crypto"}:
        auto_provider = "card"
    auto_target = os.getenv("AUTO_RENEW_INVOICE_TARGET", "all").strip().lower()
    if auto_target not in {"all", "slot"}:
        auto_target = "all"
    return {
        "auto_renew_invoice_enabled": env_bool("AUTO_RENEW_INVOICE_ENABLED", False),
        "auto_renew_invoice_hours_before": max(
            1, int(os.getenv("AUTO_RENEW_INVOICE_HOURS_BEFORE", "12"))
        ),
        "auto_renew_invoice_provider": auto_provider,
        "auto_renew_invoice_plan_key": os.getenv("AUTO_RENEW_INVOICE_PLAN_KEY", "").strip(),
        "auto_renew_invoice_target": auto_target,
    }


def _load_sub_migration_block() -> dict[str, Any]:
    return {
        "sub_migration_reminder_enabled": env_bool("SUB_MIGRATION_REMINDER_ENABLED", False),
        "sub_migration_reminder_interval_sec": max(
            300, int(os.getenv("SUB_MIGRATION_REMINDER_INTERVAL_SEC", "900"))
        ),
        "sub_migration_reminder_lookback_days": max(
            1, int(os.getenv("SUB_MIGRATION_REMINDER_LOOKBACK_DAYS", "7"))
        ),
        "sub_migration_reminder_cooldown_hours": max(
            1, int(os.getenv("SUB_MIGRATION_REMINDER_COOLDOWN_HOURS", "24"))
        ),
        "sub_migration_reminder_batch": max(
            1, min(200, int(os.getenv("SUB_MIGRATION_REMINDER_BATCH", "20")))
        ),
        "subscription_hits_retention_days": max(
            7, int(os.getenv("SUBSCRIPTION_HITS_RETENTION_DAYS", "60"))
        ),
    }


def _load_marzban_sync_audit_block() -> dict[str, Any]:
    return {
        "marzban_sync_audit_enabled": env_bool("MARZBAN_SYNC_AUDIT_ENABLED", True),
        "marzban_sync_audit_interval_sec": max(
            900, int(os.getenv("MARZBAN_SYNC_AUDIT_INTERVAL_SEC", "21600"))
        ),
        "marzban_sync_audit_limit": max(
            20, min(500, int(os.getenv("MARZBAN_SYNC_AUDIT_LIMIT", "100")))
        ),
        "marzban_sync_audit_show": max(
            1, min(30, int(os.getenv("MARZBAN_SYNC_AUDIT_SHOW", "8")))
        ),
        "marzban_sync_audit_alert_noncritical": env_bool(
            "MARZBAN_SYNC_AUDIT_ALERT_NONCRITICAL", False
        ),
    }


def _load_xray_quality_block() -> dict[str, Any]:
    return {
        "xray_error_log_path": os.getenv("XRAY_ERROR_LOG_PATH", "/var/log/xray/error.log").strip(),
        "xray_quality_monitor_enabled": env_bool("XRAY_QUALITY_MONITOR_ENABLED", False),
        "xray_quality_monitor_interval_sec": max(
            300, int(os.getenv("XRAY_QUALITY_MONITOR_INTERVAL_SEC", "900"))
        ),
        "xray_quality_monitor_window_min": max(
            1, int(os.getenv("XRAY_QUALITY_MONITOR_WINDOW_MIN", "15"))
        ),
        "xray_quality_monitor_threshold": max(
            1, int(os.getenv("XRAY_QUALITY_MONITOR_THRESHOLD", "50"))
        ),
        "xray_quality_monitor_show": max(
            1, min(30, int(os.getenv("XRAY_QUALITY_MONITOR_SHOW", "8")))
        ),
    }


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
    sub_migration_reminder_enabled: bool
    sub_migration_reminder_interval_sec: int
    sub_migration_reminder_lookback_days: int
    sub_migration_reminder_cooldown_hours: int
    sub_migration_reminder_batch: int
    subscription_hits_retention_days: int
    marzban_sync_audit_enabled: bool
    marzban_sync_audit_interval_sec: int
    marzban_sync_audit_limit: int
    marzban_sync_audit_show: int
    marzban_sync_audit_alert_noncritical: bool
    xray_error_log_path: str
    xray_quality_monitor_enabled: bool
    xray_quality_monitor_interval_sec: int
    xray_quality_monitor_window_min: int
    xray_quality_monitor_threshold: int
    xray_quality_monitor_show: int

    @staticmethod
    def load() -> "Settings":
        load_dotenv()
        _validate_required_env()
        fields: dict[str, Any] = {}
        fields.update(_load_required_block())
        fields.update(_load_marzban_block())
        fields.update(_load_network_block())
        fields.update(_load_trial_block())
        fields.update(_load_plans_block())
        fields.update(_load_cryptobot_block())
        fields.update(_load_yookassa_block())
        fields.update(_load_rate_limit_block())
        fields.update(_load_misc_block())
        fields.update(_load_ops_report_block())
        fields.update(_load_renewal_block())
        fields.update(_load_admin_alerts_block())
        fields.update(_load_auto_renew_block())
        fields.update(_load_sub_migration_block())
        fields.update(_load_marzban_sync_audit_block())
        fields.update(_load_xray_quality_block())
        return Settings(**fields)

    def cryptobot_enabled(self) -> bool:
        return bool(self.cryptobot_token)

    def yookassa_enabled(self) -> bool:
        return bool(self.yookassa_shop_id and self.yookassa_secret_key and self.yookassa_return_url)
