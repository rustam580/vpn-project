
import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
import aiosqlite
import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
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

BYTES_IN_GB = 1024**3
DEPLOY_REPORT_PATH = Path("/opt/vpn-bot/deploy/last-deploy.log")
DEPLOY_REPORT_TTL_SEC = 3600


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}




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
class Settings:
    bot_token: str
    admin_ids: set[int]
    marzban_base_url: str
    marzban_username: str
    marzban_password: str
    marzban_verify_ssl: bool
    marzban_proxy_protocol: str
    trial_days: int
    trial_gb: int
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

        return Settings(
            bot_token=bot_token,
            admin_ids=parse_admin_ids(admin_raw),
            marzban_base_url=marzban_base_url,
            marzban_username=marzban_username,
            marzban_password=marzban_password,
            marzban_verify_ssl=env_bool("MARZBAN_VERIFY_SSL", True),
            marzban_proxy_protocol=os.getenv("MARZBAN_PROXY_PROTOCOL", "vless").strip().lower(),
            trial_days=int(os.getenv("TRIAL_DAYS", "1")),
            trial_gb=int(os.getenv("TRIAL_GB", "0")),
            pay_days=int(os.getenv("PAY_DAYS", "30")),
            pay_gb=int(os.getenv("PAY_GB", "0")),
            pay_rub=float(os.getenv("PAY_RUB", "99")),
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


class Repo:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                marzban_username TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                days INTEGER NOT NULL,
                gb INTEGER NOT NULL,
                amount_rub REAL NOT NULL,
                pay_url TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(provider, external_id)
            )
            """
        )
        await self._ensure_payments_columns()
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                telegram_id INTEGER NOT NULL,
                device_id INTEGER NOT NULL,
                marzban_username TEXT NOT NULL UNIQUE,
                device_name TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id, device_id)
            )
            """
        )
        await self._ensure_devices_columns()
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invited_telegram_id INTEGER PRIMARY KEY,
                referrer_telegram_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                bonus_applied INTEGER NOT NULL DEFAULT 0,
                bonus_paid_at INTEGER
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS known_chats (
                telegram_id INTEGER PRIMARY KEY,
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                event_type TEXT NOT NULL,
                event_value TEXT,
                event_meta TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at)"
        )
        await self.conn.commit()

    async def _ensure_payments_columns(self) -> None:
        assert self.conn is not None
        c = await self.conn.execute("PRAGMA table_info(payments)")
        rows = await c.fetchall()
        await c.close()
        columns = {str(row["name"]) for row in rows}
        if "purpose" not in columns:
            await self.conn.execute(
                "ALTER TABLE payments ADD COLUMN purpose TEXT NOT NULL DEFAULT 'plan'"
            )
        if "device_slot" not in columns:
            await self.conn.execute(
                "ALTER TABLE payments ADD COLUMN device_slot INTEGER"
            )
        await self.conn.commit()

    async def _ensure_devices_columns(self) -> None:
        assert self.conn is not None
        c = await self.conn.execute("PRAGMA table_info(devices)")
        rows = await c.fetchall()
        await c.close()
        columns = {str(row["name"]) for row in rows}
        if "device_name" not in columns:
            await self.conn.execute("ALTER TABLE devices ADD COLUMN device_name TEXT")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def get_user(self, telegram_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            "SELECT telegram_id, marzban_username FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def upsert_user(self, telegram_id: int, marzban_username: str) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO users (telegram_id, marzban_username, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE
            SET marzban_username = excluded.marzban_username,
                updated_at = excluded.updated_at
            """,
            (telegram_id, marzban_username, now, now),
        )
        await self.conn.commit()
        await self.upsert_device(telegram_id, 1, marzban_username)
        await self.touch_chat(telegram_id)

    async def touch_chat(self, telegram_id: int) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO known_chats (telegram_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE
            SET last_seen_at = excluded.last_seen_at
            """,
            (telegram_id, now, now),
        )
        await self.conn.commit()

    async def upsert_payment(
        self,
        *,
        provider: str,
        external_id: str,
        telegram_id: int,
        days: int,
        gb: int,
        amount_rub: float,
        pay_url: str,
        status: str,
        purpose: str = "plan",
        device_slot: int | None = None,
    ) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO payments (
                provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_id) DO UPDATE
            SET status = excluded.status,
                pay_url = excluded.pay_url,
                purpose = excluded.purpose,
                device_slot = excluded.device_slot,
                updated_at = excluded.updated_at
            """,
            (
                provider,
                external_id,
                telegram_id,
                days,
                gb,
                amount_rub,
                pay_url,
                status,
                purpose,
                device_slot,
                now,
                now,
            ),
        )
        await self.conn.commit()

    async def get_payment(self, provider: str, external_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            FROM payments WHERE provider = ? AND external_id = ?
            """,
            (provider, external_id),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def get_payment_any(self, external_id: str) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot, created_at, updated_at
            FROM payments WHERE external_id = ?
            LIMIT 1
            """,
            (external_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def set_payment_status(self, provider: str, external_id: str, status: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "UPDATE payments SET status = ?, updated_at = ? WHERE provider = ? AND external_id = ?",
            (status, int(time.time()), provider, external_id),
        )
        await self.conn.commit()

    async def claim_payment_for_apply(self, provider: str, external_id: str) -> bool:
        assert self.conn is not None
        cur = await self.conn.execute(
            """
            UPDATE payments
            SET status = 'processing', updated_at = ?
            WHERE provider = ?
              AND external_id = ?
              AND status NOT IN ('paid_applied', 'processing')
            """,
            (int(time.time()), provider, external_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def list_users(self) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            "SELECT telegram_id, marzban_username FROM users ORDER BY created_at DESC"
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_known_telegram_ids(self) -> list[int]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id FROM known_chats
            UNION
            SELECT telegram_id FROM users
            UNION
            SELECT telegram_id FROM devices
            UNION
            SELECT telegram_id FROM payments
            ORDER BY telegram_id ASC
            """
        )
        rows = await c.fetchall()
        await c.close()
        result: list[int] = []
        for row in rows:
            tg_id = row["telegram_id"]
            if tg_id is None:
                continue
            try:
                result.append(int(tg_id))
            except (TypeError, ValueError):
                continue
        return result

    async def get_device(self, telegram_id: int, device_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id, device_id, marzban_username, device_name
            FROM devices
            WHERE telegram_id = ? AND device_id = ?
            """,
            (telegram_id, device_id),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def list_devices(self, telegram_id: int) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT telegram_id, device_id, marzban_username, device_name
            FROM devices
            WHERE telegram_id = ?
            ORDER BY device_id ASC
            """,
            (telegram_id,),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def upsert_device(
        self,
        telegram_id: int,
        device_id: int,
        marzban_username: str,
        device_name: str | None = None,
    ) -> None:
        assert self.conn is not None
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO devices (telegram_id, device_id, marzban_username, device_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id, device_id) DO UPDATE
            SET marzban_username = excluded.marzban_username,
                device_name = COALESCE(excluded.device_name, devices.device_name),
                updated_at = excluded.updated_at
            """,
            (telegram_id, device_id, marzban_username, device_name, now, now),
        )
        await self.conn.commit()

    async def set_device_name(self, telegram_id: int, device_id: int, device_name: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE devices
            SET device_name = ?, updated_at = ?
            WHERE telegram_id = ? AND device_id = ?
            """,
            (device_name, int(time.time()), telegram_id, device_id),
        )
        await self.conn.commit()

    async def list_device_usernames(self, telegram_id: int) -> list[str]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT marzban_username
            FROM devices
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        rows = await c.fetchall()
        await c.close()
        seen: set[str] = set()
        result: list[str] = []
        for row in rows:
            name = str(row["marzban_username"])
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    async def payment_status_counts(self) -> dict[str, int]:
        assert self.conn is not None
        c = await self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM payments GROUP BY status"
        )
        rows = await c.fetchall()
        await c.close()
        result: dict[str, int] = {}
        for row in rows:
            result[str(row["status"])] = int(row["cnt"])
        return result

    async def log_event(
        self,
        *,
        event_type: str,
        telegram_id: int | None = None,
        event_value: str = "",
        event_meta: dict[str, Any] | None = None,
    ) -> None:
        assert self.conn is not None
        meta_raw = ""
        if event_meta:
            try:
                meta_raw = json.dumps(event_meta, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                meta_raw = ""
        await self.conn.execute(
            """
            INSERT INTO events (telegram_id, event_type, event_value, event_meta, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                event_type.strip()[:80],
                event_value.strip()[:250],
                meta_raw,
                int(time.time()),
            ),
        )
        await self.conn.commit()

    async def event_counts_since(self, since_ts: int) -> dict[str, dict[str, int]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                event_type,
                COUNT(*) AS total,
                COUNT(DISTINCT telegram_id) AS users
            FROM events
            WHERE created_at >= ?
            GROUP BY event_type
            """,
            (since_ts,),
        )
        rows = await c.fetchall()
        await c.close()
        data: dict[str, dict[str, int]] = {}
        for row in rows:
            key = str(row["event_type"] or "").strip()
            if not key:
                continue
            data[key] = {
                "total": int(row["total"] or 0),
                "users": int(row["users"] or 0),
            }
        return data

    async def get_latest_payment(self, telegram_id: int) -> dict[str, Any] | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, purpose, device_slot, days, gb, amount_rub, status, updated_at
            FROM payments
            WHERE telegram_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        return dict(row) if row else None

    async def has_paid_plan_payment(self, telegram_id: int) -> bool:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT 1
            FROM payments
            WHERE telegram_id = ?
              AND status = 'paid_applied'
              AND purpose IN ('plan', 'plan_device', 'plan_all')
            LIMIT 1
            """,
            (telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        return row is not None

    async def list_unfinished_crypto_payments(self, limit: int = 100) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot
            FROM payments
            WHERE provider = 'crypto'
              AND status NOT IN ('paid_applied', 'canceled', 'expired', 'processing')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def list_unfinished_payments(
        self, provider: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, days, gb, amount_rub, pay_url, status, purpose, device_slot
            FROM payments
            WHERE provider = ?
              AND status NOT IN ('paid_applied', 'canceled', 'expired', 'processing')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (provider, limit),
        )
        rows = await c.fetchall()
        await c.close()
        return [dict(row) for row in rows]

    async def requeue_stuck_processing_payments(
        self, provider: str, *, older_than_sec: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        assert self.conn is not None
        cutoff = int(time.time()) - max(60, older_than_sec)
        c = await self.conn.execute(
            """
            SELECT provider, external_id, telegram_id, purpose, device_slot, updated_at
            FROM payments
            WHERE provider = ?
              AND status = 'processing'
              AND updated_at <= ?
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (provider, cutoff, limit),
        )
        rows = await c.fetchall()
        await c.close()
        if not rows:
            return []
        ids = [(str(row["provider"]), str(row["external_id"])) for row in rows]
        for p, ext_id in ids:
            await self.conn.execute(
                """
                UPDATE payments
                SET status = 'pending', updated_at = ?
                WHERE provider = ? AND external_id = ? AND status = 'processing'
                """,
                (int(time.time()), p, ext_id),
            )
        await self.conn.commit()
        return [dict(row) for row in rows]

    async def bind_referrer(self, invited_telegram_id: int, referrer_telegram_id: int) -> str:
        assert self.conn is not None
        if invited_telegram_id == referrer_telegram_id:
            return "self"

        c = await self.conn.execute(
            "SELECT referrer_telegram_id FROM referrals WHERE invited_telegram_id = ?",
            (invited_telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        if row:
            if int(row["referrer_telegram_id"]) == referrer_telegram_id:
                return "exists_same"
            return "exists_other"

        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO referrals (invited_telegram_id, referrer_telegram_id, created_at, bonus_applied)
            VALUES (?, ?, ?, 0)
            """,
            (invited_telegram_id, referrer_telegram_id, now),
        )
        await self.conn.commit()
        return "bound"

    async def get_referral_stats(self, referrer_telegram_id: int) -> dict[str, int]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN bonus_applied = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM referrals
            WHERE referrer_telegram_id = ?
            """,
            (referrer_telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        total = int((row["total"] if row and row["total"] is not None else 0) or 0)
        rewarded = int((row["rewarded"] if row and row["rewarded"] is not None else 0) or 0)
        return {
            "total": total,
            "rewarded": rewarded,
            "pending": max(total - rewarded, 0),
        }

    async def get_referral_global_stats(self) -> dict[str, int]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN bonus_applied = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM referrals
            """
        )
        row = await c.fetchone()
        await c.close()
        total = int((row["total"] if row and row["total"] is not None else 0) or 0)
        rewarded = int((row["rewarded"] if row and row["rewarded"] is not None else 0) or 0)
        return {
            "total": total,
            "rewarded": rewarded,
            "pending": max(total - rewarded, 0),
        }

    async def list_top_referrers(self, limit: int = 10) -> list[dict[str, Any]]:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT
                r.referrer_telegram_id AS telegram_id,
                u.marzban_username AS marzban_username,
                COUNT(*) AS total,
                SUM(CASE WHEN r.bonus_applied = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM referrals r
            LEFT JOIN users u ON u.telegram_id = r.referrer_telegram_id
            GROUP BY r.referrer_telegram_id
            ORDER BY rewarded DESC, total DESC, r.referrer_telegram_id ASC
            LIMIT ?
            """,
            (max(1, limit),),
        )
        rows = await c.fetchall()
        await c.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            total = int((row["total"] if row["total"] is not None else 0) or 0)
            rewarded = int((row["rewarded"] if row["rewarded"] is not None else 0) or 0)
            result.append(
                {
                    "telegram_id": int(row["telegram_id"]),
                    "marzban_username": str(row["marzban_username"] or "").strip(),
                    "total": total,
                    "rewarded": rewarded,
                    "pending": max(total - rewarded, 0),
                }
            )
        return result

    async def claim_referral_bonus(self, invited_telegram_id: int) -> int | None:
        assert self.conn is not None
        c = await self.conn.execute(
            """
            SELECT referrer_telegram_id
            FROM referrals
            WHERE invited_telegram_id = ?
              AND bonus_applied = 0
            """,
            (invited_telegram_id,),
        )
        row = await c.fetchone()
        await c.close()
        if not row:
            return None

        referrer_telegram_id = int(row["referrer_telegram_id"])
        now = int(time.time())
        cur = await self.conn.execute(
            """
            UPDATE referrals
            SET bonus_applied = 1, bonus_paid_at = ?
            WHERE invited_telegram_id = ?
              AND bonus_applied = 0
            """,
            (now, invited_telegram_id),
        )
        await self.conn.commit()
        if cur.rowcount > 0:
            return referrer_telegram_id
        return None

    async def rollback_referral_bonus_claim(self, invited_telegram_id: int, referrer_telegram_id: int) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE referrals
            SET bonus_applied = 0, bonus_paid_at = NULL
            WHERE invited_telegram_id = ?
              AND referrer_telegram_id = ?
              AND bonus_applied = 1
            """,
            (invited_telegram_id, referrer_telegram_id),
        )
        await self.conn.commit()


class InMemoryRateLimiter:
    def __init__(self, limit: int, window_sec: int):
        self.limit = max(1, limit)
        self.window_sec = max(1, window_sec)
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._events.get(key)
        if dq is None:
            dq = deque()
            self._events[key] = dq
        cutoff = now - self.window_sec
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True


class MarzbanClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.marzban_base_url,
            timeout=20.0,
            verify=settings.marzban_verify_ssl,
        )
        self.token: str | None = None

    async def close(self) -> None:
        await self.client.aclose()

    async def auth(self) -> None:
        r = await self.client.post(
            "/api/admin/token",
            data={"username": self.settings.marzban_username, "password": self.settings.marzban_password},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Marzban auth failed: {r.status_code} {r.text}")
        token = r.json().get("access_token")
        if not token:
            raise RuntimeError("Marzban auth failed: access_token missing")
        self.token = token

    async def req(self, method: str, path: str, *, allow_404: bool = False, **kwargs: Any) -> Any:
        if not self.token:
            await self.auth()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.token}"
        r = await self.client.request(method, path, headers=headers, **kwargs)
        if r.status_code == 401:
            await self.auth()
            headers["Authorization"] = f"Bearer {self.token}"
            r = await self.client.request(method, path, headers=headers, **kwargs)
        if r.status_code == 404 and allow_404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"Marzban API error: {r.status_code} {r.text}")
        return r.json() if r.content else None

    async def get_user(self, username: str) -> dict[str, Any] | None:
        return await self.req("GET", f"/api/user/{username}", allow_404=True)

    async def get_inbound_tags(self, protocol: str) -> list[str]:
        data = await self.req("GET", "/api/inbounds")
        items = data.get(protocol, []) if isinstance(data, dict) else []
        tags = [item["tag"] for item in items if item.get("tag")]
        if not tags:
            raise RuntimeError(f"No inbounds for protocol '{protocol}'")
        return tags

    async def create_user(self, *, username: str, expire: int, data_limit: int) -> dict[str, Any]:
        protocol = self.settings.marzban_proxy_protocol
        payload = {
            "username": username,
            "status": "active",
            "expire": expire,
            "data_limit": data_limit,
            "data_limit_reset_strategy": "no_reset",
            "proxies": {protocol: {}},
            "inbounds": {protocol: await self.get_inbound_tags(protocol)},
        }
        return await self.req("POST", "/api/user", json=payload)

    async def modify_user(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.req("PUT", f"/api/user/{username}", json=payload)


def is_admin(telegram_id: int | None, settings: Settings) -> bool:
    return telegram_id is not None and telegram_id in settings.admin_ids


def keyboard_for_user(*, is_admin: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="🔑 Получить конфиг"), KeyboardButton(text="💳 Купить доступ")],
        [KeyboardButton(text="📊 Мой статус"), KeyboardButton(text="📂 Еще")],
        [KeyboardButton(text="🆘 Поддержка")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Админ-кабинет")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def more_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📱 Добавить устройство", callback_data="quick:device"),
                InlineKeyboardButton(text="🔁 Заменить устройство", callback_data="quick:replace"),
            ],
            [
                InlineKeyboardButton(text="✏️ Переименовать устройство", callback_data="quick:rename"),
                InlineKeyboardButton(text="🎁 Рефералка", callback_data="quick:ref"),
            ],
            [
                InlineKeyboardButton(text="❓ FAQ", callback_data="quick:faq"),
                InlineKeyboardButton(text="📢 Наш канал", callback_data="quick:channel"),
            ],
            [InlineKeyboardButton(text="⚠️ Проблема с подключением", callback_data="quick:issue")],
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="admin:ref_top")],
            [InlineKeyboardButton(text="🧰 Ops отчет", callback_data="admin:ops")],
            [InlineKeyboardButton(text="🚀 Обновить и проверить", callback_data="admin:deploy")],
            [InlineKeyboardButton(text="🔎 Найти пользователя", callback_data="admin:find_user")],
            [InlineKeyboardButton(text="➕ Устройство", callback_data="admin:device_add")],
            [
                InlineKeyboardButton(
                    text="🔁 Заменить устройство",
                    switch_inline_query_current_chat="/device_replace ",
                )
            ],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
            [
                InlineKeyboardButton(
                    text="🎁 Выдать доступ",
                    switch_inline_query_current_chat="/grant ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Проверить платеж",
                    switch_inline_query_current_chat="/check ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Отключить доступ",
                    switch_inline_query_current_chat="/disable ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Привязать аккаунт",
                    switch_inline_query_current_chat="/link ",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Реф-бонус вручную",
                    switch_inline_query_current_chat="/ref_grant ",
                )
            ],
            [InlineKeyboardButton(text="📨 Шаблоны поддержки", callback_data="admin:support_templates")],
            [InlineKeyboardButton(text="📘 Шпаргалка", callback_data="admin:help")],
        ]
    )


def payment_methods_keyboard(
    settings: Settings,
    *,
    target: str,
    device_id: int = 1,
) -> InlineKeyboardMarkup:
    suffix = "all" if target == "all" else f"slot:{device_id}"
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(
                text=("₿ CryptoBot" if settings.cryptobot_enabled() else "₿ CryptoBot (не настроен)"),
                callback_data=f"buy:crypto:{suffix}",
            )
        ]
    )
    if settings.yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Карта (YooKassa)", callback_data=f"buy:card:{suffix}")])
    if not settings.yookassa_enabled():
        rows.append(
            [InlineKeyboardButton(text="💳 Оплата картой (не настроена)", callback_data=f"buy:card:{suffix}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_target_keyboard(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    ordered = sorted(devices, key=lambda d: int(d["device_id"]))
    for row in ordered:
        device_id = int(row["device_id"])
        label = _device_label(device_id, row.get("device_name"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔑 Продлить {_short_label(label, limit=22)}",
                    callback_data=f"buyselect:slot:{device_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🧩 Продлить все ключи", callback_data="buyselect:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def device_methods_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(
                text=("₿ CryptoBot" if settings.cryptobot_enabled() else "₿ CryptoBot (не настроен)"),
                callback_data="device:crypto",
            )
        ]
    )
    if settings.yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Карта (YooKassa)", callback_data="device:card")])
    if not settings.yookassa_enabled():
        rows.append([InlineKeyboardButton(text="💳 Оплата картой (не настроена)", callback_data="device:card")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def enabled_payment_providers(settings: Settings) -> list[str]:
    providers: list[str] = []
    if settings.cryptobot_enabled():
        providers.append("crypto")
    if settings.yookassa_enabled():
        providers.append("card")
    return providers


def pay_action_keyboard(provider: str, external_id: str, pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть оплату", url=pay_url)],
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check:{provider}:{external_id}")],
        ]
    )


def broadcast_confirm_keyboard(*, fmt_key: str, with_buttons: bool) -> InlineKeyboardMarkup:
    fmt_label = broadcast_format_label(fmt_key)
    buttons_label = "Кнопки: вкл" if with_buttons else "Кнопки: выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="admin:broadcast_send"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin:broadcast_cancel"),
            ],
            [InlineKeyboardButton(text=f"Формат: {fmt_label}", callback_data="admin:broadcast_fmt")],
            [InlineKeyboardButton(text=buttons_label, callback_data="admin:broadcast_btn")],
        ]
    )


def broadcast_format_label(fmt_key: str) -> str:
    if fmt_key == "markdown":
        return "Markdown"
    if fmt_key == "html":
        return "HTML"
    return "Текст"


def broadcast_parse_mode(fmt_key: str) -> str | None:
    if fmt_key == "markdown":
        return "Markdown"
    if fmt_key == "html":
        return "HTML"
    return None


def broadcast_next_format(fmt_key: str) -> str:
    order = ["plain", "markdown", "html"]
    if fmt_key not in order:
        return "plain"
    idx = (order.index(fmt_key) + 1) % len(order)
    return order[idx]


def format_used(v: int) -> str:
    return f"{max(v, 0) / BYTES_IN_GB:.1f} GB"


def format_limit(v: int) -> str:
    return "Без лимита" if v <= 0 else f"{v / BYTES_IN_GB:.1f} GB"


def plan_gb_text(gb: int) -> str:
    return "Безлимит" if gb <= 0 else f"{gb} GB"


def plan_gb_for_desc(gb: int) -> str:
    return "UNLIM" if gb <= 0 else f"{gb}GB"


def format_expire(v: int) -> str:
    if v <= 0:
        return "Без срока"
    return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def format_time_left(expire_ts: int) -> str:
    if expire_ts <= 0:
        return "Без ограничения по сроку"
    now = int(time.time())
    delta = expire_ts - now
    if delta <= 0:
        return "Срок истек"
    days = delta // 86400
    hours = (delta % 86400) // 3600
    if days > 0:
        return f"{days} дн. {hours} ч."
    return f"{hours} ч."


def format_last_online(raw: Any) -> str:
    if raw is None:
        return "нет данных"
    text = str(raw).strip()
    if not text:
        return "нет данных"
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except Exception:
        return text


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


def status_text(user: dict[str, Any]) -> str:
    links = extract_links(user)
    expire_ts = int(user.get("expire", 0) or 0)
    status = str(user.get("status", "unknown"))
    status_icon = "🟢" if status == "active" else "⚪"
    cfg_count = 1 if links else 0
    return (
        f"👤 <b>Пользователь:</b> {user.get('username', 'unknown')}\n"
        f"{status_icon} <b>Статус:</b> {status}\n"
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
    await message.answer("🔑 Ваш конфиг (1 устройство):")
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
    "DEVICE_LIMIT": "int",
    "DEVICE_ADD_RUB": "float",
    "REFERRAL_BONUS_DAYS": "int",
    "SUPPORT_USERNAME": "str",
    "SUPPORT_TEXT": "str",
    "CHANNEL_URL": "str",
    "DEPLOY_BROADCAST_USERS": "bool",
    "OPS_REPORT_ENABLED": "bool",
    "OPS_REPORT_HOUR": "int",
    "OPS_REPORT_MINUTE": "int",
    "YOOKASSA_POLL_SECONDS": "int",
    "YOOKASSA_SHOP_ID": "str",
    "YOOKASSA_SECRET_KEY": "str",
    "YOOKASSA_RETURN_URL": "str",
    "PAYMENT_PROCESSING_REQUEUE_SECONDS": "int",
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


def _read_iface_bytes(iface: str) -> tuple[int, int] | None:
    try:
        data = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    needle = f"{iface}:"
    for line in data:
        line = line.strip()
        if not line.startswith(needle):
            continue
        payload = line.split(":", 1)[1].split()
        if len(payload) < 16:
            return None
        rx = int(payload[0])
        tx = int(payload[8])
        return rx, tx
    return None


async def measure_iface_mbps(iface: str, duration: int = 5) -> float | None:
    start = _read_iface_bytes(iface)
    if not start:
        return None
    await asyncio.sleep(max(1, duration))
    end = _read_iface_bytes(iface)
    if not end:
        return None
    delta_bytes = (end[0] + end[1]) - (start[0] + start[1])
    if delta_bytes < 0:
        return None
    return (delta_bytes * 8) / (duration * 1024 * 1024)


def _parse_sar_dev_output(raw_text: str, target_iface: str) -> float | None:
    rx = 0.0
    tx = 0.0
    count = 0
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if (
            low.startswith("linux")
            or "iface" in low
            or low.startswith("average:")
            or low.startswith("среднее:")
            or low.startswith("средн.:")
        ):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            iface_idx = parts.index(target_iface)
        except ValueError:
            continue
        # Expected columns after IFACE:
        # rxpck/s txpck/s rxkB/s txkB/s ...
        if len(parts) <= iface_idx + 4:
            continue
        try:
            rx_kbps = float(parts[iface_idx + 3])
            tx_kbps = float(parts[iface_idx + 4])
        except ValueError:
            continue
        rx += rx_kbps
        tx += tx_kbps
        count += 1
    if count == 0:
        return None
    avg_kbps = (rx + tx) / count
    return (avg_kbps * 8) / 1024


async def measure_iface_mbps_sar(iface: str, duration: int = 60) -> float | None:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    try:
        proc = await asyncio.create_subprocess_exec(
            "sar",
            "-n",
            "DEV",
            "1",
            str(duration),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=duration + 5)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if not stdout:
        return None
    text = stdout.decode("utf-8", errors="ignore")
    return _parse_sar_dev_output(text, iface)


def _configs_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup | None:
    if not items:
        return None
    if len(items) > 8:
        return None
    rows: list[list[InlineKeyboardButton]] = []
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
        links = extract_links(user)
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
        links = extract_links(user)
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
        await message.answer("⚠️ Активные конфиги не найдены.")
        return

    lines: list[str] = []
    index_map: list[tuple[int, str, str]] = []
    counter = 1
    for device_id, label, link in items:
        lines.append(f"{counter}. {label}: {_link_preview(link)}")
        index_map.append((counter, label, link))
        counter += 1

    await message.answer(
        f"🔑 Активные конфиги: {len(items)}.\n"
        "Короткий список (для ориентира):\n" + "\n".join(lines)
    )

    file_lines: list[str] = []
    for device_id, label, link in items:
        header = label if label.startswith("Устройство") else f"Устройство {device_id} — {label}"
        file_lines.append(header)
        file_lines.append(link)
        file_lines.append("")
    payload = "\n".join(file_lines).strip() + "\n"
    try:
        await message.answer_document(
            BufferedInputFile(payload.encode("utf-8"), filename="configs.txt"),
            caption="Полный список конфигов в одном файле.",
        )
    except Exception:
        logging.exception("Failed to send configs.txt to user %s", telegram_id)
        await message.answer("Не удалось отправить файл. Показываю все конфиги в чате.")
        await send_configs_in_chat(message, items)

    cfg_buttons = _configs_keyboard([(idx, label) for idx, label, _ in index_map])
    if cfg_buttons:
        await message.answer("Показать конфиг в чате:", reply_markup=cfg_buttons)
    await message.answer(config_import_hint_text(), parse_mode="HTML")


def _render_config_block(label: str, link: str) -> str:
    safe_label = html.escape(label)
    safe_link = html.escape(link)
    return f"{safe_label}:\n<code>{safe_link}</code>"


async def send_configs_in_chat(message: Message, items: list[tuple[int, str, str]]) -> None:
    if not items:
        await message.answer("⚠️ Активные конфиги не найдены.")
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
        await bot.send_message(telegram_id, "⚠️ Активные конфиги не найдены.")
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
        await bot.send_message(telegram_id, "⚠️ Активные конфиги не найдены.")
        return
    lines: list[str] = []
    index_map: list[tuple[int, str]] = []
    counter = 1
    for _, label, link in items:
        lines.append(f"{counter}. {label}: {_link_preview(link)}")
        index_map.append((counter, label))
        counter += 1
    await bot.send_message(
        telegram_id,
        f"🔑 Активные конфиги: {len(items)}.\n"
        "Короткий список (для ориентира):\n" + "\n".join(lines),
    )
    file_lines: list[str] = []
    for device_id, label, link in items:
        header = label if label.startswith("Устройство") else f"Устройство {device_id} — {label}"
        file_lines.append(header)
        file_lines.append(link)
        file_lines.append("")
    payload = "\n".join(file_lines).strip() + "\n"
    try:
        await bot.send_document(
            telegram_id,
            BufferedInputFile(payload.encode("utf-8"), filename="configs.txt"),
            caption="Полный список конфигов в одном файле.",
        )
    except Exception:
        logging.exception("Failed to send configs.txt via bot to user %s", telegram_id)
        await bot.send_message(telegram_id, "Не удалось отправить файл. Показываю все конфиги в чате.")
        await send_configs_in_chat_to_bot(bot=bot, telegram_id=telegram_id, items=items)
    cfg_buttons = _configs_keyboard(index_map)
    if cfg_buttons:
        await bot.send_message(telegram_id, "Показать конфиг в чате:", reply_markup=cfg_buttons)
    await bot.send_message(telegram_id, config_import_hint_text(), parse_mode="HTML")


async def ensure_user(
    *,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    create_if_missing: bool,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    created = False
    row = await repo.get_user(telegram_id)
    if row:
        username = row["marzban_username"]
        user = await marzban.get_user(username)
        if user:
            await repo.upsert_user(telegram_id, username)
            return username, user, created

    username = build_username(telegram_id)
    user = await marzban.get_user(username)
    if user:
        await repo.upsert_user(telegram_id, username)
        return username, user, created

    if not create_if_missing:
        return None, None, created

    user = await marzban.create_user(
        username=username,
        expire=int(time.time()) + settings.trial_days * 24 * 3600,
        data_limit=0 if settings.trial_gb <= 0 else settings.trial_gb * BYTES_IN_GB,
    )
    await repo.upsert_user(telegram_id, username)
    created = True
    return username, user, created


async def ensure_device(
    *,
    telegram_id: int,
    device_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    create_if_missing: bool,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    if device_id < 1:
        return None, None, False
    if settings.device_limit > 0 and device_id > settings.device_limit:
        return None, None, False

    if device_id == 1:
        return await ensure_user(
            telegram_id=telegram_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=create_if_missing,
        )

    created = False
    row = await repo.get_device(telegram_id, device_id)
    if row:
        username = row["marzban_username"]
        user = await marzban.get_user(username)
        if user:
            return username, user, created

    username = build_device_username(telegram_id, device_id)
    user = await marzban.get_user(username)
    if user:
        await repo.upsert_device(telegram_id, device_id, username)
        return username, user, created

    if not create_if_missing:
        return None, None, created

    primary_user = None
    primary_row = await repo.get_user(telegram_id)
    if primary_row:
        primary_user = await marzban.get_user(primary_row["marzban_username"])

    if primary_user:
        expire = int(primary_user.get("expire", 0) or 0)
        data_limit = int(primary_user.get("data_limit", 0) or 0)
        status = str(primary_user.get("status", "active"))
        user = await marzban.create_user(
            username=username,
            expire=expire,
            data_limit=data_limit,
        )
        if status and status != "active":
            await marzban.modify_user(username, {"status": status})
    else:
        user = await marzban.create_user(
            username=username,
            expire=int(time.time()) + settings.trial_days * 24 * 3600,
            data_limit=0 if settings.trial_gb <= 0 else settings.trial_gb * BYTES_IN_GB,
        )
    await repo.upsert_device(telegram_id, device_id, username)
    created = True
    return username, user, created


async def extend_access(
    *,
    telegram_id: int,
    days: int,
    gb: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> dict[str, Any]:
    username, user, _ = await ensure_user(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=False,
    )
    if username is None:
        username = build_username(telegram_id)
        user = await marzban.get_user(username)

    if user:
        old_exp = int(user.get("expire", 0) or 0)
        if days <= 0:
            new_exp = 0
        else:
            new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
        if gb <= 0:
            # Explicit unlimited plan removes any existing cap.
            new_limit = 0
        else:
            old_limit = int(user.get("data_limit", 0) or 0)
            new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB
        updated = await marzban.modify_user(
            username,
            {"expire": new_exp, "data_limit": new_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=0 if days <= 0 else int(time.time()) + days * 24 * 3600,
            data_limit=0 if gb <= 0 else gb * BYTES_IN_GB,
        )
    await repo.upsert_user(telegram_id, username)
    return updated


async def extend_access_all_devices(
    *,
    telegram_id: int,
    days: int,
    gb: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> dict[str, Any]:
    usernames = await repo.list_device_usernames(telegram_id)
    if not usernames:
        username, _, _ = await ensure_device(
            telegram_id=telegram_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=True,
        )
        if not username:
            return {}
        usernames = [username]

    updated_primary: dict[str, Any] | None = None
    for username in usernames:
        user = await marzban.get_user(username)
        if user:
            old_exp = int(user.get("expire", 0) or 0)
            if days <= 0:
                new_exp = 0
            else:
                new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
            if gb <= 0:
                new_limit = 0
            else:
                old_limit = int(user.get("data_limit", 0) or 0)
                new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB
            updated = await marzban.modify_user(
                username,
                {"expire": new_exp, "data_limit": new_limit, "status": "active"},
            )
        else:
            updated = await marzban.create_user(
                username=username,
                expire=0 if days <= 0 else int(time.time()) + days * 24 * 3600,
                data_limit=0 if gb <= 0 else gb * BYTES_IN_GB,
            )
        if updated_primary is None:
            updated_primary = updated
    return updated_primary or {}


async def extend_access_device(
    *,
    telegram_id: int,
    device_id: int,
    days: int,
    gb: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> dict[str, Any]:
    username, user, _ = await ensure_device(
        telegram_id=telegram_id,
        device_id=device_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=True,
    )
    if not username:
        raise RuntimeError("Не удалось получить слот устройства.")

    if user:
        old_exp = int(user.get("expire", 0) or 0)
        if days <= 0:
            new_exp = 0
        else:
            new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
        if gb <= 0:
            new_limit = 0
        else:
            old_limit = int(user.get("data_limit", 0) or 0)
            new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB
        updated = await marzban.modify_user(
            username,
            {"expire": new_exp, "data_limit": new_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=0 if days <= 0 else int(time.time()) + days * 24 * 3600,
            data_limit=0 if gb <= 0 else gb * BYTES_IN_GB,
        )
    await repo.upsert_device(telegram_id, device_id, username)
    return updated


async def sync_expire_across_devices(
    *,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    mode: str = "max",
    source_slot: int | None = None,
) -> tuple[int, int, int, int]:
    """
    Align expire timestamp for all known device slots.
    Returns: (target_expire, changed_count, found_count, missing_count)
    """
    rows = await repo.list_devices(telegram_id)
    if not rows:
        primary = await repo.get_user(telegram_id)
        if primary:
            rows = [
                {
                    "device_id": 1,
                    "marzban_username": str(primary.get("marzban_username") or "").strip(),
                    "device_name": None,
                }
            ]
        else:
            return 0, 0, 0, 0

    found: list[tuple[int, str, int]] = []
    missing_count = 0
    for row in rows:
        device_id = int(row.get("device_id") or 0)
        username = str(row.get("marzban_username") or "").strip()
        if not username:
            missing_count += 1
            continue
        user = await marzban.get_user(username)
        if not user:
            missing_count += 1
            continue
        expire = int(user.get("expire", 0) or 0)
        found.append((device_id, username, expire))

    if not found:
        return 0, 0, 0, missing_count

    if mode == "min":
        target_expire = min(expire for _, _, expire in found)
    elif mode == "slot":
        if source_slot is None or source_slot < 1:
            raise ValueError("Укажите корректный слот для синхронизации.")
        slot_expire = next((expire for d_id, _, expire in found if d_id == source_slot), None)
        if slot_expire is None:
            raise ValueError(f"Слот {source_slot} не найден в Marzban.")
        target_expire = slot_expire
    else:
        target_expire = max(expire for _, _, expire in found)

    changed = 0
    for _, username, expire in found:
        if expire == target_expire:
            continue
        await marzban.modify_user(username, {"expire": target_expire})
        changed += 1

    return target_expire, changed, len(found), missing_count


async def set_permanent_access(
    *,
    telegram_id: int,
    gb: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> dict[str, Any]:
    username, user, _ = await ensure_user(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=False,
    )
    if username is None:
        username = build_username(telegram_id)
        user = await marzban.get_user(username)

    if gb <= 0:
        new_limit = 0
    else:
        old_limit = int(user.get("data_limit", 0) or 0) if user else 0
        new_limit = old_limit + gb * BYTES_IN_GB if old_limit > 0 else gb * BYTES_IN_GB

    if user:
        updated = await marzban.modify_user(
            username,
            {"expire": 0, "data_limit": new_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=0,
            data_limit=new_limit,
        )
    await repo.upsert_user(telegram_id, username)
    return updated


async def extend_access_days_only(
    *,
    telegram_id: int,
    days: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> dict[str, Any]:
    username, user, _ = await ensure_user(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
        create_if_missing=False,
    )
    if username is None:
        username = build_username(telegram_id)
        user = await marzban.get_user(username)

    if user:
        old_exp = int(user.get("expire", 0) or 0)
        old_limit = int(user.get("data_limit", 0) or 0)
        new_exp = max(int(time.time()), old_exp) + days * 24 * 3600
        updated = await marzban.modify_user(
            username,
            {"expire": new_exp, "data_limit": old_limit, "status": "active"},
        )
    else:
        updated = await marzban.create_user(
            username=username,
            expire=int(time.time()) + days * 24 * 3600,
            data_limit=0 if settings.trial_gb <= 0 else settings.trial_gb * BYTES_IN_GB,
        )
    await repo.upsert_user(telegram_id, username)
    return updated


async def apply_referral_bonus_if_needed(
    *,
    paid_telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
    bot: Bot | None = None,
) -> None:
    bonus_days = max(0, settings.referral_bonus_days)
    if bonus_days <= 0:
        return

    referrer_id = await repo.claim_referral_bonus(paid_telegram_id)
    if referrer_id is None:
        return

    try:
        updated = await extend_access_days_only(
            telegram_id=referrer_id,
            days=bonus_days,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
    except Exception:
        await repo.rollback_referral_bonus_claim(paid_telegram_id, referrer_id)
        raise

    if bot is None:
        return
    try:
        await notify_access_updated(
            bot,
            referrer_id,
            updated,
            f"🎁 Реферальный бонус: +{bonus_days} дн. за приглашенного пользователя.",
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
    except Exception:
        logging.exception("Referral bonus: failed to notify referrer %s", referrer_id)


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


def _event_users(summary: dict[str, dict[str, int]], key: str) -> int:
    item = summary.get(key) or {}
    return int(item.get("users", 0) or 0)


async def build_funnel_24h_text(repo: Repo) -> str:
    since_ts = int(time.time()) - 86400
    summary = await repo.event_counts_since(since_ts)
    start_users = _event_users(summary, "user_start")
    config_users = _event_users(summary, "config_requested")
    trial_users = _event_users(summary, "trial_issued")
    pay_create_users = _event_users(summary, "payment_created_plan")
    pay_apply_users = _event_users(summary, "payment_paid_plan")
    issue_users = _event_users(summary, "issue_reported")

    if start_users <= 0 and config_users <= 0 and pay_create_users <= 0 and pay_apply_users <= 0:
        return "Воронка 24ч:\n- данных пока нет"

    config_conv = (config_users / start_users * 100.0) if start_users > 0 else 0.0
    pay_conv = (pay_apply_users / start_users * 100.0) if start_users > 0 else 0.0
    checkout_conv = (pay_apply_users / pay_create_users * 100.0) if pay_create_users > 0 else 0.0
    return (
        "Воронка 24ч:\n"
        f"- Стартов: {start_users}\n"
        f"- Получили конфиг: {config_users} ({config_conv:.1f}% от стартов)\n"
        f"- Выдано триалов: {trial_users}\n"
        f"- Создали платеж (тариф): {pay_create_users}\n"
        f"- Оплатили тариф: {pay_apply_users} ({pay_conv:.1f}% от стартов, {checkout_conv:.1f}% от checkout)\n"
        f"- Жалобы на подключение: {issue_users}"
    )


async def build_admin_stats_text(repo: Repo, marzban: MarzbanClient) -> str:
    rows = await repo.list_users()
    total_local = len(rows)
    active = 0
    disabled = 0
    expired = 0
    expiring_24h = 0
    no_expire = 0
    missing = 0
    now = int(time.time())

    for row in rows:
        user = await marzban.get_user(row["marzban_username"])
        if not user:
            missing += 1
            continue

        status = str(user.get("status", "unknown"))
        if status == "active":
            active += 1
        elif status == "disabled":
            disabled += 1

        expire = int(user.get("expire", 0) or 0)
        if expire <= 0:
            no_expire += 1
        elif expire <= now:
            expired += 1
        elif expire <= now + 86400:
            expiring_24h += 1

    pay_counts = await repo.payment_status_counts()
    pending = pay_counts.get("pending", 0)
    paid_applied = pay_counts.get("paid_applied", 0)
    ref_counts = await repo.get_referral_global_stats()

    funnel_text = await build_funnel_24h_text(repo)
    db_tip = ""
    if active >= 300:
        db_tip = "\n\n⚠️ Рекомендация: активных пользователей много, запланируйте миграцию с SQLite на Postgres."

    return (
        "Статистика:\n"
        f"- Пользователей в локальной БД: {total_local}\n"
        f"- Активных: {active}\n"
        f"- Отключенных: {disabled}\n"
        f"- Истекших: {expired}\n"
        f"- Истекают за 24ч: {expiring_24h}\n"
        f"- Без срока: {no_expire}\n"
        f"- Не найдены в Marzban: {missing}\n\n"
        "Платежи:\n"
        f"- pending: {pending}\n"
        f"- paid_applied: {paid_applied}\n\n"
        "Рефералка:\n"
        f"- Всего приглашений: {ref_counts['total']}\n"
        f"- Бонус выдан: {ref_counts['rewarded']}\n"
        f"- Ожидают первую оплату: {ref_counts['pending']}\n\n"
        f"{funnel_text}"
        f"{db_tip}"
    )


async def build_ref_top_text(repo: Repo, limit: int = 10) -> str:
    ref_counts = await repo.get_referral_global_stats()
    top_rows = await repo.list_top_referrers(limit=limit)
    lines = [
        "Рефералка:",
        f"- Всего приглашений: {ref_counts['total']}",
        f"- Бонус выдан: {ref_counts['rewarded']}",
        f"- Ожидают первую оплату: {ref_counts['pending']}",
        "",
        f"Топ {max(1, limit)} рефереров:",
    ]
    if not top_rows:
        lines.append("- пока нет данных")
        return "\n".join(lines)
    for i, row in enumerate(top_rows, start=1):
        username = row["marzban_username"] or "-"
        lines.append(
            f"{i}. tg:{row['telegram_id']} ({username})"
            f" — приглашено {row['total']}, бонусов {row['rewarded']}, ждут оплаты {row['pending']}"
        )
    return "\n".join(lines)


def _bytes_to_human(n: int) -> str:
    step = 1024.0
    value = float(max(n, 0))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < step or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"


def _service_state(name: str) -> str:
    try:
        p = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        state = (p.stdout or p.stderr).strip().splitlines()
        return state[0] if state else "unknown"
    except Exception:
        return "unknown"


def _read_meminfo() -> tuple[int, int] | None:
    try:
        total_kb = 0
        avail_kb = 0
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
        if total_kb <= 0:
            return None
        used_kb = max(total_kb - avail_kb, 0)
        return total_kb * 1024, used_kb * 1024
    except Exception:
        return None


def _latest_backup_path() -> str | None:
    try:
        backup_dir = Path("/opt/backups/vpn-bot")
        files = sorted(backup_dir.glob("vpn-bot-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        return str(files[0])
    except Exception:
        return None


async def build_ops_report_text(
    settings: Settings, marzban: MarzbanClient, *, sar_seconds: int = 10
) -> str:
    def collect() -> str:
        now_utc = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        try:
            up_seconds = int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))
            up_days = up_seconds // 86400
            up_hours = (up_seconds % 86400) // 3600
            up_text = f"{up_days}д {up_hours}ч"
        except Exception:
            up_text = "n/a"

        try:
            la = os.getloadavg()
            load_text = f"{la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}"
        except Exception:
            load_text = "n/a"

        disk = shutil.disk_usage("/")
        disk_text = f"{_bytes_to_human(disk.used)} из {_bytes_to_human(disk.total)}"

        mem = _read_meminfo()
        mem_text = (
            f"{_bytes_to_human(mem[1])} из {_bytes_to_human(mem[0])}" if mem else "n/a"
        )

        vpn_bot_state = _service_state("vpn-bot")
        caddy_state = _service_state("caddy")
        fail2ban_state = _service_state("fail2ban")

        backup_text = _latest_backup_path() or "не найден"

        return (
            "Ops отчет:\n"
            f"- Время: {now_utc}\n"
            f"- Uptime: {up_text}\n"
            f"- Load avg: {load_text}\n"
            f"- RAM: {mem_text}\n"
            f"- Диск /: {disk_text}\n"
            f"- vpn-bot: {vpn_bot_state}\n"
            f"- caddy: {caddy_state}\n"
            f"- fail2ban: {fail2ban_state}\n"
            f"- Последний backup: {backup_text}"
        )

    base = await asyncio.to_thread(collect)
    online_users = 0
    try:
        stats = await marzban.req("GET", "/api/system")
        online_users = int(stats.get("online_users", 0))
    except Exception:
        logging.exception("Ops report: failed to fetch /api/system")
    iface_mbps: float | None = None
    iface_mbps_60: float | None = None
    try:
        iface_mbps = await asyncio.wait_for(
            measure_iface_mbps(settings.net_iface, duration=5),
            timeout=8,
        )
    except Exception:
        logging.exception("Ops report: iface measure failed")
    if sar_seconds > 0:
        try:
            iface_mbps_60 = await asyncio.wait_for(
                measure_iface_mbps_sar(settings.net_iface, duration=sar_seconds),
                timeout=sar_seconds + 8,
            )
        except Exception:
            logging.exception("Ops report: iface sar measure failed")
    capacity_lines = [
        "Оценка емкости:",
        f"- Интерфейс: {settings.net_iface}",
        f"- Скорость порта: {settings.port_speed_mbps:.0f} Mbps",
        f"- Используемо: {settings.port_speed_mbps * settings.port_utilization:.0f} Mbps",
        f"- Онлайн сейчас: {online_users}",
    ]
    if iface_mbps is None or iface_mbps < 0.1 or online_users <= 0:
        capacity_lines.append("- Текущая нагрузка: недостаточно данных")
    else:
        avg_per_user = iface_mbps / max(online_users, 1)
        usable = settings.port_speed_mbps * settings.port_utilization
        max_concurrent = int(usable / max(avg_per_user, 0.1))
        ratio = settings.concurrency_ratio if settings.concurrency_ratio > 0 else 0.05
        total_est = int(max_concurrent / ratio) if ratio > 0 else 0
        capacity_lines.extend(
            [
                f"- Текущая нагрузка (avg 5s): {iface_mbps:.2f} Mbps",
                f"- Текущая нагрузка (avg {sar_seconds}s): {iface_mbps_60:.2f} Mbps"
                if iface_mbps_60 is not None
                else f"- Текущая нагрузка (avg {sar_seconds}s): n/a",
                f"- Средний на онлайн-юзера: {avg_per_user:.2f} Mbps",
                f"- Оценка одновременных: ~{max_concurrent}",
                f"- Оценка всего при {ratio*100:.0f}% онлайн: ~{total_est}",
            ]
        )
    return base + "\n" + "\n".join(capacity_lines)


async def build_payments_summary(repo: Repo) -> str:
    counts = await repo.payment_status_counts()
    if not counts:
        return "Платежи: данных нет"
    lines = ["Платежи:"]
    for status, cnt in sorted(counts.items()):
        lines.append(f"- {status}: {cnt}")
    return "\n".join(lines)


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
    interval = max(10, settings.cryptobot_poll_seconds)
    while not stop_event.is_set():
        try:
            if settings.cryptobot_enabled():
                requeued = await repo.requeue_stuck_processing_payments(
                    "crypto",
                    older_than_sec=settings.payment_processing_requeue_seconds,
                    limit=100,
                )
                if requeued:
                    logging.warning(
                        "Auto crypto: requeued %s stuck processing payments",
                        len(requeued),
                    )
                    try:
                        await notify_admin_requeued_processing(
                            bot=bot,
                            settings=settings,
                            provider="crypto",
                            rows=requeued,
                            older_than_sec=settings.payment_processing_requeue_seconds,
                        )
                    except Exception:
                        logging.exception("Auto crypto: requeue notify failed")
                payments = await repo.list_unfinished_crypto_payments(limit=100)
                for payment in payments:
                    external_id = str(payment["external_id"])
                    try:
                        status = await cryptobot_check_invoice(settings, external_id)
                    except Exception:
                        logging.exception("Auto check failed for crypto payment %s", external_id)
                        continue

                    if status == "paid":
                        try:
                            updated, purpose, _ = await apply_paid_payment(
                                provider="crypto",
                                external_id=external_id,
                                payment=payment,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                                bot=bot,
                                strict_device_slot=False,
                            )
                        except Exception:
                            logging.exception("Auto check: failed to apply payment %s", external_id)
                            await repo.set_payment_status("crypto", external_id, status)
                            continue
                        try:
                            if purpose == "device_add":
                                slot = int(payment.get("device_slot") or 0)
                                text = (
                                    f"✅ Оплата подтверждена. Устройство {slot} добавлено.\n"
                                    f"Назовите его командой: /device_name {slot} Мой ноутбук"
                                )
                            elif purpose == "plan_device":
                                slot = int(payment.get("device_slot") or 0)
                                text = f"✅ Оплата подтверждена автоматически. Устройство {slot} продлено."
                            elif purpose == "plan_all":
                                text = "✅ Оплата подтверждена автоматически. Все ключи продлены."
                            else:
                                text = "Оплата подтверждена автоматически. Доступ продлен."
                            await notify_access_updated(
                                bot,
                                int(payment["telegram_id"]),
                                updated,
                                text,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                            )
                        except Exception:
                            logging.exception(
                                "Auto check: failed to notify user %s for payment %s",
                                payment["telegram_id"],
                                external_id,
                            )
                    else:
                        await repo.set_payment_status("crypto", external_id, status)
        except Exception:
            logging.exception("Auto crypto worker iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def yookassa_auto_worker(
    *,
    settings: Settings,
    repo: Repo,
    marzban: MarzbanClient,
    bot: Bot,
    stop_event: asyncio.Event,
) -> None:
    interval = max(20, settings.yookassa_poll_seconds)
    while not stop_event.is_set():
        try:
            if settings.yookassa_enabled():
                requeued = await repo.requeue_stuck_processing_payments(
                    "card",
                    older_than_sec=settings.payment_processing_requeue_seconds,
                    limit=100,
                )
                if requeued:
                    logging.warning(
                        "Auto yookassa: requeued %s stuck processing payments",
                        len(requeued),
                    )
                    try:
                        await notify_admin_requeued_processing(
                            bot=bot,
                            settings=settings,
                            provider="card",
                            rows=requeued,
                            older_than_sec=settings.payment_processing_requeue_seconds,
                        )
                    except Exception:
                        logging.exception("Auto yookassa: requeue notify failed")
                payments = await repo.list_unfinished_payments("card", limit=100)
                for payment in payments:
                    external_id = str(payment.get("external_id") or "").strip()
                    if not external_id:
                        continue
                    status = await yookassa_check_payment(settings, external_id)
                    paid = status == "succeeded"
                    if paid:
                        claimed = await repo.claim_payment_for_apply("card", external_id)
                        if not claimed:
                            continue
                        try:
                            updated, purpose, _ = await apply_paid_payment(
                                provider="card",
                                external_id=external_id,
                                payment=payment,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                                bot=bot,
                                strict_device_slot=False,
                            )
                            if purpose == "device_add":
                                slot = int(payment.get("device_slot") or 0)
                                text = (
                                    f"✅ Оплата подтверждена. Устройство {slot} добавлено.\n"
                                    f"Назовите его командой: /device_name {slot} Мой ноутбук"
                                )
                            elif purpose == "plan_device":
                                slot = int(payment.get("device_slot") or 0)
                                text = f"✅ Оплата подтверждена автоматически. Устройство {slot} продлено."
                            elif purpose == "plan_all":
                                text = "✅ Оплата подтверждена автоматически. Все ключи продлены."
                            else:
                                text = "Оплата подтверждена автоматически. Доступ продлен."
                            await notify_access_updated(
                                bot,
                                int(payment["telegram_id"]),
                                updated,
                                text,
                                repo=repo,
                                marzban=marzban,
                                settings=settings,
                            )
                        except Exception:
                            logging.exception("Auto yookassa: failed to apply payment %s", external_id)
                            await repo.set_payment_status("card", external_id, status)
                    else:
                        await repo.set_payment_status("card", external_id, status)
        except Exception:
            logging.exception("Auto yookassa worker iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


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

    async def send_user_lookup(message: Message, target_id: int) -> None:
        if target_id <= 0:
            await message.answer("ID должен быть положительным числом.")
            return
        chat = None
        try:
            chat = await message.bot.get_chat(target_id)
        except Exception:
            pass

        lines: list[str] = []
        link = f'<a href="tg://user?id={target_id}">ID {target_id}</a>'
        lines.append(f"👤 Пользователь: {link}")
        if chat is not None:
            name_parts = [chat.first_name or "", chat.last_name or ""]
            name = " ".join(p for p in name_parts if p).strip()
            if name:
                lines.append(f"Имя: {html.escape(name)}")
            username = str(chat.username or "").strip()
            if username:
                lines.append(f"Username: @{html.escape(username)}")

        row = await repo.get_user(target_id)
        marzban_user = None
        if row:
            username = str(row["marzban_username"])
            marzban_user = await marzban.get_user(username)
            if marzban_user:
                lines.append(f"Marzban: {html.escape(username)}")
            else:
                lines.append(f"Marzban: {html.escape(username)} (не найден)")
        else:
            guessed = build_username(target_id)
            marzban_user = await marzban.get_user(guessed)
            if marzban_user:
                lines.append(f"Marzban: {html.escape(guessed)}")
            else:
                lines.append("Marzban: не найден")

        if marzban_user:
            expire_ts = int(marzban_user.get("expire", 0) or 0)
            data_limit = int(marzban_user.get("data_limit", 0) or 0)
            used = int(marzban_user.get("used_traffic", 0) or 0)
            status = str(marzban_user.get("status", "unknown"))
            lines.append(f"Статус: {html.escape(status)}")
            lines.append(f"Действует до: {format_expire(expire_ts)}")
            lines.append(f"Трафик: {format_used(used)} из {format_limit(data_limit)}")

        devices = await repo.list_devices(target_id)
        if devices:
            lines.append("Устройства:")
            for row in devices:
                device_id = int(row["device_id"])
                label = _device_label(device_id, row.get("device_name"))
                username = str(row.get("marzban_username") or "")
                mz_user = await marzban.get_user(username) if username else None
                if not mz_user:
                    state = "не найден в Marzban"
                else:
                    state = (
                        f"{mz_user.get('status', 'unknown')}, "
                        f"online: {format_last_online(mz_user.get('online_at') or mz_user.get('last_online') or mz_user.get('last_online_at'))}, "
                        f"traffic: {format_used(int(mz_user.get('used_traffic', 0) or 0))}"
                    )
                if label.startswith("Устройство"):
                    lines.append(
                        f"- {device_id}. {html.escape(label)} ({html.escape(username)}): {html.escape(state)}"
                    )
                else:
                    lines.append(
                        f"- {device_id}. Устройство {device_id} — {html.escape(label)} ({html.escape(username)}): {html.escape(state)}"
                    )
        else:
            lines.append("Устройства: нет")

        latest_payment = await repo.get_latest_payment(target_id)
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
                f"Последний платеж: {html.escape(provider)}, {html.escape(purpose)}, "
                f"{amount:.2f} RUB, {html.escape(status)}, {updated_text}"
            )
        else:
            lines.append("Последний платеж: нет данных")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть диалог", url=f"tg://user?id={target_id}")]
            ]
        )
        text = "\n".join(lines)
        try:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest as exc:
            # Some users restrict profile linking in buttons. Fallback to plain message.
            if "BUTTON_USER_PRIVACY_RESTRICTED" not in str(exc):
                raise
            await message.answer(
                text + "\n\n⚠️ Кнопка «Открыть диалог» недоступна из-за privacy-настроек пользователя.",
                parse_mode="HTML",
            )

    async def send_broadcast_preview(message: Message, body: str) -> None:
        if not message.from_user:
            return
        admin_id = int(message.from_user.id)
        users = await repo.list_users()
        targets = {int(row["telegram_id"]) for row in users if row.get("telegram_id") is not None}
        count = len(targets)
        fmt_key = pending_broadcast_format.get(admin_id, "plain")
        with_buttons = pending_broadcast_buttons.get(admin_id, True)
        fmt_label = broadcast_format_label(fmt_key)
        buttons_label = "вкл" if with_buttons else "выкл"
        preview = (
            f"📣 Рассылка (получателей: {count})\n"
            f"Формат: {fmt_label}\n"
            f"Кнопки: {buttons_label}\n\n"
            f"{body}"
        )
        parse_mode = broadcast_parse_mode(fmt_key)
        kwargs: dict[str, Any] = {}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        try:
            await message.answer(
                preview,
                reply_markup=broadcast_confirm_keyboard(fmt_key=fmt_key, with_buttons=with_buttons),
                **kwargs,
            )
        except Exception:
            logging.exception("Broadcast preview failed")
            await message.answer(
                "Не удалось показать предпросмотр с форматированием. Проверьте разметку или выберите «Текст».",
                reply_markup=broadcast_confirm_keyboard(fmt_key=fmt_key, with_buttons=with_buttons),
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

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        if tg_id is not None:
            payload = extract_start_payload(message.text)
            referrer_id = parse_referrer_from_payload(payload)
            if referrer_id is not None:
                bind_result = await repo.bind_referrer(
                    invited_telegram_id=tg_id,
                    referrer_telegram_id=referrer_id,
                )
                if bind_result == "bound":
                    await message.answer("Реферальная привязка сохранена. Бонус начислим после вашей первой оплаты.")
                elif bind_result == "self":
                    await message.answer("Нельзя указать себя как реферера.")

        await message.answer(
            build_start_text(
                trial_days=settings.trial_days,
                trial_gb_text=plan_gb_text(settings.trial_gb),
                pay_days=settings.pay_days,
                pay_gb_text=plan_gb_text(settings.pay_gb),
                pay_rub=settings.pay_rub,
                device_limit_text=format_device_limit(settings.device_limit),
            ),
            reply_markup=keyboard_for_user(is_admin=is_admin(tg_id, settings)),
            parse_mode="HTML",
        )
        if tg_id is not None:
            await track_event("user_start", telegram_id=tg_id)

    @router.message(F.text.contains("/grant_perm"))
    async def grant_perm_any(message: Message) -> None:
        if await handle_grant_perm(message):
            return

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        providers = enabled_payment_providers(settings)
        check_hint = (
            "<code>/check &lt;" + "|".join(providers) + "&gt; &lt;payment_id&gt;</code> — проверить оплату"
            if providers
            else "<code>/check</code> — провайдеры оплаты не настроены"
        )
        is_admin_user = bool(message.from_user and is_admin(int(message.from_user.id), settings))

        user_block = (
            "<b>👤 Команды пользователя</b>\n"
            "• <code>/config</code> — получить/обновить конфиги\n"
            "• <code>/guide</code> — инструкция по подключению\n"
            "• <code>/diag</code> — диагностика подключения\n"
            "• <code>/buy</code> — продлить доступ\n"
            "• <code>/replace</code> — переиздать конфиг устройства\n"
            "• <code>/ref</code> — реферальная ссылка\n"
            f"• {check_hint}\n"
            "• <code>/faq</code> — частые вопросы\n"
            "• <code>/support</code> — поддержка\n"
            "• <code>/channel</code> — наш канал"
        )

        if not is_admin_user:
            await message.answer(user_block, parse_mode="HTML")
            return

        admin_block = (
            "<b>🛠 Команды администратора</b>\n"
            "• <code>/admin</code> — админ-кабинет\n"
            "• <code>/admin_stats</code> — краткая статистика\n"
            "• <code>/ref_stats [telegram_id]</code> — реф-статистика\n"
            "• <code>/ref_grant &lt;telegram_id&gt; [days]</code> — реф-бонус вручную\n"
            "• <code>/grant &lt;telegram_id&gt; &lt;days&gt; &lt;gb&gt;</code> — доступ всем слотам\n"
            "• <code>/grant_perm &lt;telegram_id&gt; [gb]</code> — бессрочный доступ\n"
            "• <code>/grant_device &lt;telegram_id&gt; &lt;slot&gt; &lt;days&gt; &lt;gb&gt;</code>\n"
            "• <code>/sync_expire &lt;telegram_id&gt; [max|min|slot:&lt;id&gt;]</code>\n"
            "• <code>/device_add &lt;telegram_id&gt; [slot]</code>\n"
            "• <code>/device_replace &lt;telegram_id&gt; &lt;slot&gt;</code>\n"
            "• <code>/disable &lt;telegram_id&gt;</code>\n"
            "• <code>/link &lt;telegram_id&gt; &lt;marzban_username&gt;</code>\n"
            "• <code>/ops</code> — health-отчет"
        )
        await message.answer(user_block + "\n\n" + admin_block, parse_mode="HTML")

    @router.message(Command("ref"))
    async def ref_cmd(message: Message) -> None:
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

    @router.message(Command("faq"))
    async def faq_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(build_user_faq_text(), parse_mode="HTML")

    @router.message(Command("guide"))
    async def guide_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        await message.answer(quick_connect_guide_text(), parse_mode="HTML")

    @router.message(Command("support"))
    async def support_cmd(message: Message) -> None:
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

    @router.message(Command("channel"))
    async def channel_cmd(message: Message) -> None:
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

    @router.message(Command("menu"))
    async def menu_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        tg_id = int(message.from_user.id) if message.from_user else None
        await message.answer(
            "Меню обновлено.",
            reply_markup=keyboard_for_user(is_admin=is_admin(tg_id, settings)),
        )

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
            await message.answer("❗ Профиль не найден. Нажмите «Получить конфиг».")
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
            lines.append("Профиль не найден. Нажмите «🔑 Получить конфиг».")
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
                f"💳 Продление одного устройства: {settings.pay_rub:.2f} RUB за {settings.pay_days} дней.\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(settings, target="slot", device_id=1),
            )
            return
        tg_id = int(message.from_user.id)
        devices = await repo.list_devices(tg_id)
        if not devices:
            await message.answer(
                f"💳 Продление одного устройства: {settings.pay_rub:.2f} RUB за {settings.pay_days} дней.\n"
                "Сначала будет продлен основной ключ (устройство 1).\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(settings, target="slot", device_id=1),
            )
            return
        if len(devices) == 1:
            only_slot = int(devices[0]["device_id"])
            await message.answer(
                f"💳 Продление одного устройства: {settings.pay_rub:.2f} RUB за {settings.pay_days} дней.\n"
                f"Будет продлен слот {only_slot}.\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(settings, target="slot", device_id=only_slot),
            )
            return
        total = settings.pay_rub * len(devices)
        await message.answer(
            f"💳 Продление одного устройства: {settings.pay_rub:.2f} RUB за {settings.pay_days} дней.\n"
            f"💳 Продление всех ключей ({len(devices)} шт): {total:.2f} RUB.\n"
            "Выберите, что продлить:",
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
            await message.answer("❗ Сначала получите основной конфиг.")
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
            await message.answer("Активные устройства не найдены. Сначала получите конфиг.")
            return
        kb = _devices_replace_keyboard(devices)
        await message.answer(
            "Выберите устройство для переиздания конфига.\n"
            "Старый конфиг выбранного устройства будет отключен.",
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
            await message.answer("Устройства не найдены. Сначала получите конфиг.")
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
            await message.answer("Устройство не найдено. Сначала получите конфиг.")
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
        else:
            await message.answer("📊 Ваш текущий доступ:")
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
            await message.answer("Устройства не найдены. Сначала получите конфиг.")
            return
        kb = _devices_rename_keyboard(devices)
        await message.answer("Выберите устройство для переименования:", reply_markup=kb)

    @router.message(F.text == "🎁 Рефералка")
    async def ref_btn(message: Message) -> None:
        await ref_cmd(message)

    @router.message(F.text == "❓ FAQ")
    async def faq_btn(message: Message) -> None:
        await faq_cmd(message)

    @router.message(F.text == "🆘 Поддержка")
    async def support_btn(message: Message) -> None:
        await support_cmd(message)

    @router.message(F.text == "📢 Наш канал")
    async def channel_btn(message: Message) -> None:
        await channel_cmd(message)

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

    @router.callback_query(F.data.startswith("quick:"))
    async def quick_action_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        action = callback.data.split(":", 1)[1].strip()
        tg_id = int(callback.from_user.id)

        if action == "device":
            row = await repo.get_user(tg_id)
            if not row:
                await callback.answer()
                await callback.message.answer("❗ Сначала получите основной конфиг.")
                return
            devices = await repo.list_devices(tg_id)
            if settings.device_limit > 0 and len(devices) >= settings.device_limit:
                await callback.answer()
                await callback.message.answer("Лимит устройств уже исчерпан.")
                return
            if not await repo.has_paid_plan_payment(tg_id):
                await callback.answer()
                await callback.message.answer(
                    "📱 Доп. устройство доступно только после оплаты основного тарифа.\n"
                    "Сначала нажмите «Купить доступ»."
                )
                return
            await callback.answer()
            await callback.message.answer(
                f"📱 Доп. устройство: {settings.device_add_rub:.2f} RUB.\n"
                "Оплата добавляет только новый слот устройства.\n"
                "Срок доступа не продлевается.\n"
                "После оплаты устройство появится автоматически.\n"
                "Название можно задать через «Переименовать устройство».",
                reply_markup=device_methods_keyboard(settings),
            )
            return

        if action == "replace":
            devices = await list_replaceable_devices(tg_id)
            await callback.answer()
            if not devices:
                await callback.message.answer("Активные устройства не найдены. Сначала получите конфиг.")
                return
            kb = _devices_replace_keyboard(devices)
            await callback.message.answer(
                "Выберите устройство для переиздания конфига.\n"
                "Старый конфиг выбранного устройства будет отключен.",
                reply_markup=kb,
            )
            return

        if action == "rename":
            devices = await repo.list_devices(tg_id)
            await callback.answer()
            if not devices:
                await callback.message.answer("Устройства не найдены. Сначала получите конфиг.")
                return
            kb = _devices_rename_keyboard(devices)
            await callback.message.answer("Выберите устройство для переименования:", reply_markup=kb)
            return

        if action == "ref":
            username = await get_bot_username(callback.message.bot)
            await callback.answer()
            if not username:
                await callback.message.answer("Не удалось получить username бота. Попробуйте позже.")
                return
            link = f"https://t.me/{username}?start=ref_{tg_id}"
            stats = await repo.get_referral_stats(tg_id)
            await callback.message.answer(
                "🎁 Реферальная программа:\n"
                f"- Бонус за оплаченного друга: +{max(0, settings.referral_bonus_days)} дн.\n"
                f"- Приглашено: {stats['total']}\n"
                f"- Бонус выдан: {stats['rewarded']}\n"
                f"- Ожидают первую оплату: {stats['pending']}\n\n"
                "Ваша ссылка:\n"
                f"{link}"
            )
            return

        if action == "faq":
            await callback.answer()
            await callback.message.answer(build_user_faq_text(), parse_mode="HTML")
            return

        if action == "channel":
            await callback.answer()
            link = normalize_channel_url(settings.channel_url)
            if link:
                await callback.message.answer(f"<b>📢 Наш канал</b>\n{link}", parse_mode="HTML")
            else:
                await callback.message.answer("Канал пока не настроен. Администратор скоро добавит ссылку.")
            return

        if action == "issue":
            pending_issue.add(tg_id)
            await callback.answer()
            await callback.message.answer(
                "Опишите проблему одним сообщением по шаблону:\n"
                "1) Время (дата и время по МСК)\n"
                "2) Устройство и приложение (iOS/Android/Windows + клиент)\n"
                "3) Что именно не работает\n"
                "4) Ошибка/скрин (если есть)\n"
                "5) Пробовали переимпорт/перезапуск\n\n"
                "Напишите «отмена» чтобы выйти."
            )
            return

        await callback.answer("Неизвестное действие", show_alert=True)

    @router.callback_query(F.data.startswith("devrename:"))
    async def device_rename_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        _, value = callback.data.split(":", 1)
        if value == "cancel":
            pending_device_rename.pop(int(callback.from_user.id), None)
            await callback.answer("Отменено")
            return
        try:
            device_id = int(value)
        except ValueError:
            await callback.answer("Неверный формат", show_alert=True)
            return
        if device_id < 1:
            await callback.answer("Неверный ID", show_alert=True)
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await callback.answer("ID вне лимита", show_alert=True)
            return
        row = await repo.get_device(int(callback.from_user.id), device_id)
        if not row:
            await callback.answer("Устройство не найдено", show_alert=True)
            return
        pending_device_rename[int(callback.from_user.id)] = device_id
        await callback.message.answer(
            f"Введите новое имя для устройства {device_id} (пример: Мой ноутбук)."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("devreplace:"))
    async def device_replace_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        _, value = callback.data.split(":", 1)
        if value == "cancel":
            await callback.answer("Отменено")
            return
        try:
            device_id = int(value)
        except ValueError:
            await callback.answer("Неверный формат", show_alert=True)
            return
        if device_id < 1:
            await callback.answer("Неверный ID", show_alert=True)
            return
        if settings.device_limit > 0 and device_id > settings.device_limit:
            await callback.answer("ID вне лимита", show_alert=True)
            return
        row = await repo.get_device(int(callback.from_user.id), device_id)
        if not row:
            await callback.answer("Устройство не найдено", show_alert=True)
            return
        username = str(row.get("marzban_username") or "").strip()
        user = await marzban.get_user(username) if username else None
        if not user or str(user.get("status", "unknown")) != "active":
            await callback.answer("Устройство не активно", show_alert=True)
            return
        label = _device_label(device_id, row.get("device_name"))
        await callback.message.answer(
            f"Подтвердите замену конфига для устройства {device_id} ({label}).\n"
            "Старый конфиг этого устройства будет отключен.",
            reply_markup=_device_replace_confirm_keyboard(device_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("devreplace_confirm:"))
    async def device_replace_confirm_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Неверный callback", show_alert=True)
            return
        _, raw_device_id, decision = parts
        try:
            device_id = int(raw_device_id)
        except ValueError:
            await callback.answer("Неверный ID", show_alert=True)
            return
        if decision != "yes":
            await callback.answer("Отменено")
            return
        if device_id < 1 or (settings.device_limit > 0 and device_id > settings.device_limit):
            await callback.answer("ID вне лимита", show_alert=True)
            return
        tg_id = int(callback.from_user.id)
        try:
            old_username, new_username, new_user = await replace_device_slot(
                telegram_id=tg_id,
                slot=device_id,
            )
        except Exception as exc:
            logging.exception("User device_replace failed for tg=%s slot=%s", tg_id, device_id)
            await callback.answer("Не удалось заменить конфиг", show_alert=True)
            await callback.message.answer(f"Ошибка замены конфига: {exc}")
            return
        await callback.answer("Готово")
        await callback.message.answer(
            f"🔁 Конфиг устройства {device_id} переиздан.\n"
            "Старый конфиг этого устройства отключен.\n"
            "Импортируйте новый конфиг из списка ниже.\n"
            "Важно: один конфиг = одно устройство."
        )
        if device_id == 1:
            await send_status(callback.message, new_user)
        await send_device_links(
            message=callback.message,
            telegram_id=tg_id,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message()
    async def fallback_menu(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user:
            return
        tg_id = int(message.from_user.id)
        if tg_id in pending_user_lookup:
            if not message.text:
                await message.answer("Введите числовой Telegram ID или «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_user_lookup.discard(tg_id)
                await message.answer("Ок, отменено.")
                return
            if text.startswith("/"):
                await message.answer("Введите Telegram ID числом или напишите «отмена».")
                return
            try:
                target_id = int(text)
            except ValueError:
                await message.answer("ID должен быть числом. Пример: 386029735")
                return
            pending_user_lookup.discard(tg_id)
            await send_user_lookup(message, target_id)
            return
        if tg_id in pending_device_add_prompt:
            if not message.text:
                await message.answer("Введите Telegram ID и слот (опционально) или «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_device_add_prompt.discard(tg_id)
                await message.answer("Ок, отменено.")
                return
            if text.startswith("/"):
                await message.answer("Введите Telegram ID и слот (опционально) или «отмена».")
                return
            parts = text.split()
            if len(parts) not in {1, 2}:
                await message.answer("Формат: <telegram_id> [slot]. Пример: 386029735 2")
                return
            try:
                target = int(parts[0])
                slot = int(parts[1]) if len(parts) == 2 else 2
            except ValueError:
                await message.answer("ID и слот должны быть числами. Пример: 386029735 2")
                return
            if slot < 1:
                await message.answer("Слот должен быть >= 1")
                return
            if settings.device_limit > 0 and slot > settings.device_limit:
                await message.answer(f"Слот должен быть 1..{settings.device_limit}")
                return
            pending_device_add_prompt.discard(tg_id)
            _, user, created = await ensure_device(
                telegram_id=target,
                device_id=slot,
                repo=repo,
                marzban=marzban,
                settings=settings,
                create_if_missing=True,
            )
            if not user:
                await message.answer("Не удалось создать устройство.")
                return
            msg = f"Устройство {slot} создано." if created else f"Устройство {slot} уже существует."
            await message.answer(msg)
            return
        if tg_id in pending_broadcast_prompt:
            if not message.text:
                await message.answer("Введите текст рассылки или «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_broadcast_prompt.discard(tg_id)
                pending_broadcast_text.pop(tg_id, None)
                pending_broadcast_format.pop(tg_id, None)
                pending_broadcast_buttons.pop(tg_id, None)
                await message.answer("Рассылка отменена.")
                return
            if text.startswith("/"):
                await message.answer("Введите текст рассылки или напишите «отмена».")
                return
            pending_broadcast_prompt.discard(tg_id)
            pending_broadcast_text[tg_id] = text
            pending_broadcast_format.setdefault(tg_id, "plain")
            pending_broadcast_buttons.setdefault(tg_id, True)
            await send_broadcast_preview(message, text)
            return
        if tg_id in pending_device_rename:
            if not message.text:
                await message.answer("Введите текстовое имя устройства.")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_device_rename.pop(tg_id, None)
                await message.answer("Переименование отменено.")
                return
            if text.startswith("/"):
                await message.answer("Введите текстовое имя устройства или напишите «отмена».")
                return
            name = normalize_device_name(text)
            if not name:
                await message.answer("Имя устройства не может быть пустым.")
                return
            device_id = pending_device_rename.pop(tg_id)
            await repo.set_device_name(tg_id, device_id, name)
            await message.answer(f"✅ Устройство {device_id} теперь называется: {name}")
            return
        if tg_id in pending_issue:
            if not message.text:
                await message.answer("Отправьте текстовое описание проблемы или напишите «отмена».")
                return
            text = message.text.strip()
            if text.lower() in {"отмена", "cancel", "/cancel"}:
                pending_issue.discard(tg_id)
                await message.answer("Ок, отменено.")
                return
            if text.startswith("/"):
                await message.answer("Отправьте описание проблемы или напишите «отмена».")
                return
            pending_issue.discard(tg_id)
            username = message.from_user.username or ""
            header = f"🚨 Проблема с подключением\nTG: {tg_id}"
            if username:
                header += f" (@{username})"
            report = f"{header}\n\n{text}"
            await track_event(
                "issue_reported",
                telegram_id=tg_id,
                event_meta={"text_len": len(text)},
            )
            for admin_id in settings.admin_ids:
                try:
                    await message.bot.send_message(int(admin_id), report)
                except Exception:
                    logging.exception("Failed to notify admin %s about issue", admin_id)
            await message.answer("Спасибо, отправили админу. Если нужно, мы уточним детали.")
            return
        if message.text and message.text.startswith("/"):
            # Let dedicated command handlers process slash-commands.
            raise SkipHandler()
        await message.answer(
            "Открыл меню.",
            reply_markup=keyboard_for_user(is_admin=is_admin(tg_id, settings)),
        )

    @router.callback_query(F.data.startswith("cfg:"))
    async def cfg_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        items = await collect_device_links(
            telegram_id=int(callback.from_user.id),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        parts = callback.data.split(":")
        if len(parts) >= 2 and parts[1] == "showall":
            await callback.message.answer("Все активные конфиги:")
            await send_configs_in_chat(callback.message, items)
            await callback.answer()
            return
        if len(parts) == 3 and parts[1] == "show":
            try:
                index = int(parts[2])
            except ValueError:
                await callback.answer("Неверный формат", show_alert=True)
                return
            selected: tuple[str, str] | None = None
            counter = 1
            for _, label, link in items:
                if counter == index:
                    selected = (label, link)
                    break
                counter += 1
            if not selected:
                await callback.answer("Конфиг не найден", show_alert=True)
                return
            await callback.message.answer(
                _render_config_block(selected[0], selected[1]),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await callback.answer()
            return
        await callback.answer("Неверный callback", show_alert=True)

    @router.callback_query(F.data.startswith("buyselect:"))
    async def buy_select_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        tg_id = int(callback.from_user.id)
        devices = await repo.list_devices(tg_id)
        parts = callback.data.split(":")
        if len(parts) < 2:
            await callback.answer("Неверный формат", show_alert=True)
            return
        if parts[1] == "all":
            if not devices:
                await callback.answer("Сначала получите конфиг.", show_alert=True)
                return
            total = settings.pay_rub * len(devices)
            await callback.message.answer(
                f"🧩 Продление всех ключей ({len(devices)} шт): {total:.2f} RUB.\n"
                f"Срок: +{settings.pay_days} дней, трафик: {plan_gb_text(settings.pay_gb)}.\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(settings, target="all"),
            )
            await callback.answer()
            return
        if parts[1] == "slot" and len(parts) >= 3:
            try:
                slot = int(parts[2])
            except ValueError:
                await callback.answer("Неверный слот", show_alert=True)
                return
            row = await repo.get_device(tg_id, slot)
            if not row:
                await callback.answer("Слот не найден", show_alert=True)
                return
            await callback.message.answer(
                f"🔑 Продление устройства {slot}: {settings.pay_rub:.2f} RUB.\n"
                f"Срок: +{settings.pay_days} дней, трафик: {plan_gb_text(settings.pay_gb)}.\n"
                "Выберите способ оплаты:",
                reply_markup=payment_methods_keyboard(settings, target="slot", device_id=slot),
            )
            await callback.answer()
            return
        await callback.answer("Неверный формат", show_alert=True)

    @router.callback_query(F.data.startswith("buy:"))
    async def buy_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        provider = parts[1] if len(parts) >= 2 else ""
        tg_id = int(callback.from_user.id)
        try:
            target = "slot"
            slot = 1
            if len(parts) >= 3:
                if parts[2] == "all":
                    target = "all"
                elif parts[2] == "slot" and len(parts) >= 4:
                    try:
                        slot = int(parts[3])
                    except ValueError:
                        await callback.answer("Неверный слот", show_alert=True)
                        return

            if target == "slot":
                if slot < 1:
                    await callback.answer("Неверный слот", show_alert=True)
                    return
                if slot > 1:
                    slot_row = await repo.get_device(tg_id, slot)
                    if not slot_row:
                        await callback.answer("Слот не найден", show_alert=True)
                        return
                amount_rub = settings.pay_rub
                purpose = "plan_device"
                device_slot = slot
                pay_desc = f"VPN продление устройства {slot}: +{settings.pay_days}d"
                pay_title = f"Продление устройства {slot}"
            else:
                devices = await repo.list_devices(tg_id)
                if not devices:
                    await callback.answer("Сначала получите конфиг.", show_alert=True)
                    return
                amount_rub = settings.pay_rub * len(devices)
                purpose = "plan_all"
                device_slot = 0
                pay_desc = f"VPN продление всех устройств ({len(devices)} шт): +{settings.pay_days}d"
                pay_title = f"Продление всех устройств ({len(devices)} шт)"

            if provider == "crypto":
                if not settings.cryptobot_enabled():
                    await callback.answer("CryptoBot не настроен", show_alert=True)
                    return
                external_id, pay_url = await cryptobot_create_invoice(
                    settings,
                    tg_id,
                    amount_rub=amount_rub,
                    description=pay_desc,
                )
            elif provider == "card":
                if not settings.yookassa_enabled():
                    await callback.answer("YooKassa не настроена", show_alert=True)
                    return
                external_id, pay_url = await yookassa_create_payment(
                    settings,
                    tg_id,
                    amount_rub=amount_rub,
                    description=pay_desc,
                )
            else:
                await callback.answer("Неизвестный метод", show_alert=True)
                return

            await repo.upsert_payment(
                provider=provider,
                external_id=external_id,
                telegram_id=tg_id,
                days=settings.pay_days,
                gb=settings.pay_gb,
                amount_rub=amount_rub,
                pay_url=pay_url,
                status="pending",
                purpose=purpose,
                device_slot=device_slot,
            )
            await track_event(
                "payment_created_plan",
                telegram_id=tg_id,
                event_value=provider,
                event_meta={
                    "external_id": external_id,
                    "amount_rub": amount_rub,
                    "purpose": purpose,
                    "device_slot": device_slot,
                },
            )
            await callback.message.answer(
                f"✅ Платеж создан ({provider}).\n"
                f"Тип: {pay_title}\n"
                f"Сумма: {amount_rub:.2f} RUB\n"
                f"ID: {external_id}",
                reply_markup=pay_action_keyboard(provider, external_id, pay_url),
            )
            await callback.answer()
        except Exception as exc:
            logging.exception("Create payment failed")
            await callback.answer(f"Ошибка: {exc}", show_alert=True)

    @router.callback_query(F.data.startswith("device:"))
    async def device_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        provider = callback.data.split(":", 1)[1]
        tg_id = int(callback.from_user.id)
        try:
            devices = await repo.list_devices(tg_id)
            if settings.device_limit > 0 and len(devices) >= settings.device_limit:
                await callback.answer("Лимит устройств исчерпан", show_alert=True)
                return
            if not await repo.has_paid_plan_payment(tg_id):
                await callback.answer(
                    "Сначала оплатите основной тариф, затем добавляйте устройства.",
                    show_alert=True,
                )
                return
            used_slots = {int(d["device_id"]) for d in devices}
            slot = next_device_slot(used_slots, settings.device_limit)
            if slot is None:
                await callback.answer("Нет свободных слотов", show_alert=True)
                return

            if provider == "crypto":
                if not settings.cryptobot_enabled():
                    await callback.answer("CryptoBot не настроен", show_alert=True)
                    return
                external_id, pay_url = await cryptobot_create_invoice(
                    settings,
                    tg_id,
                    amount_rub=settings.device_add_rub,
                    description=f"VPN добавление устройства {slot}",
                )
            elif provider == "card":
                if not settings.yookassa_enabled():
                    await callback.answer("YooKassa не настроена", show_alert=True)
                    return
                external_id, pay_url = await yookassa_create_payment(
                    settings,
                    tg_id,
                    amount_rub=settings.device_add_rub,
                    description=f"VPN добавление устройства {slot}",
                )
            else:
                await callback.answer("Неизвестный метод", show_alert=True)
                return

            await repo.upsert_payment(
                provider=provider,
                external_id=external_id,
                telegram_id=tg_id,
                days=0,
                gb=0,
                amount_rub=settings.device_add_rub,
                pay_url=pay_url,
                status="pending",
                purpose="device_add",
                device_slot=slot,
            )
            await track_event(
                "payment_created_device",
                telegram_id=tg_id,
                event_value=provider,
                event_meta={
                    "external_id": external_id,
                    "slot": slot,
                    "amount_rub": settings.device_add_rub,
                },
            )
            await callback.message.answer(
                f"✅ Платеж за устройство создан ({provider}).\n"
                f"ID: {external_id}\n"
                f"Слот: {slot}\n"
                "Срок доступа не продлевается.\n"
                "Важно: один конфиг = одно устройство.",
                reply_markup=pay_action_keyboard(provider, external_id, pay_url),
            )
            await callback.answer()
        except Exception as exc:
            logging.exception("Device payment create failed")
            await callback.answer(f"Ошибка: {exc}", show_alert=True)

    @router.callback_query(F.data.startswith("check:"))
    async def check_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Неверный callback", show_alert=True)
            return
        _, provider, external_id = parts
        try:
            result, updated = await check_and_apply_payment(
                provider=provider,
                external_id=external_id,
                telegram_id=int(callback.from_user.id),
                repo=repo,
                marzban=marzban,
                settings=settings,
                bot=callback.bot,
            )
            await callback.message.answer(result)
            if updated:
                await send_status(callback.message, updated)
                await send_device_links(
                    message=callback.message,
                    telegram_id=int(callback.from_user.id),
                    repo=repo,
                    marzban=marzban,
                    settings=settings,
                )
            await callback.answer("Готово")
        except Exception as exc:
            logging.exception("Check payment failed")
            await callback.answer(f"Ошибка: {exc}", show_alert=True)

    @router.callback_query(F.data.startswith("admin:"))
    async def admin_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        if not is_admin(int(callback.from_user.id), settings):
            await callback.answer("Недостаточно прав.", show_alert=True)
            return
        action = callback.data.split(":", 1)[1]
        if action == "stats":
            await callback.answer("Считаю статистику...")
            try:
                text = await asyncio.wait_for(build_admin_stats_text(repo, marzban), timeout=25)
                await callback.message.answer(text)
            except asyncio.TimeoutError:
                await callback.message.answer("Слишком долго считаю статистику. Попробуйте /admin_stats.")
            except Exception as exc:
                logging.exception("Admin stats callback failed")
                await callback.message.answer(f"Ошибка статистики: {exc}")
            return
        if action == "ops":
            await callback.answer("Собираю отчет...")
            try:
                text = await asyncio.wait_for(
                    build_ops_report_text(settings, marzban, sar_seconds=10),
                    timeout=20,
                )
                await callback.message.answer(text)
            except asyncio.TimeoutError:
                await callback.message.answer("Ops-отчет собирается слишком долго. Попробуйте /ops.")
            except Exception as exc:
                logging.exception("Ops callback failed")
                await callback.message.answer(f"Ошибка ops-отчета: {exc}")
            return
        if action == "deploy":
            await callback.answer("Запускаю deploy...")
            script = Path("/usr/local/sbin/vpn-ops-deploy")
            if not script.exists():
                await callback.message.answer("Скрипт /usr/local/sbin/vpn-ops-deploy не найден.")
                return
            if start_deploy(script):
                await callback.message.answer(
                    "🚀 Deploy запущен. Результат пришлю после перезапуска."
                )
                asyncio.create_task(schedule_deploy_report(callback.message.bot))
            else:
                await callback.message.answer("Не удалось запустить deploy.")
            return
        if action == "find_user":
            await callback.answer("Ок")
            pending_user_lookup.add(int(callback.from_user.id))
            await callback.message.answer(
                "Введите Telegram ID пользователя (пример: 386029735) или «отмена»."
            )
            return
        if action == "device_add":
            await callback.answer("Ок")
            pending_device_add_prompt.add(int(callback.from_user.id))
            await callback.message.answer(
                "Введите Telegram ID и слот (опционально), пример: 386029735 2. Или «отмена»."
            )
            return
        if action == "broadcast":
            await callback.answer("Ок")
            pending_broadcast_prompt.add(int(callback.from_user.id))
            pending_broadcast_format.setdefault(int(callback.from_user.id), "plain")
            pending_broadcast_buttons.setdefault(int(callback.from_user.id), True)
            await callback.message.answer("Введите текст рассылки или «отмена».")
            return
        if action == "broadcast_fmt":
            admin_id = int(callback.from_user.id)
            current = pending_broadcast_format.get(admin_id, "plain")
            pending_broadcast_format[admin_id] = broadcast_next_format(current)
            body = pending_broadcast_text.get(admin_id, "").strip()
            if not body:
                await callback.answer("Сначала введите текст рассылки.")
                return
            await callback.answer("Формат обновлен")
            await send_broadcast_preview(callback.message, body)
            return
        if action == "broadcast_btn":
            admin_id = int(callback.from_user.id)
            current = pending_broadcast_buttons.get(admin_id, True)
            pending_broadcast_buttons[admin_id] = not current
            body = pending_broadcast_text.get(admin_id, "").strip()
            if not body:
                await callback.answer("Сначала введите текст рассылки.")
                return
            await callback.answer("Кнопки обновлены")
            await send_broadcast_preview(callback.message, body)
            return
        if action == "broadcast_cancel":
            await callback.answer("Отменено")
            pending_broadcast_prompt.discard(int(callback.from_user.id))
            pending_broadcast_text.pop(int(callback.from_user.id), None)
            pending_broadcast_format.pop(int(callback.from_user.id), None)
            pending_broadcast_buttons.pop(int(callback.from_user.id), None)
            await callback.message.answer("Рассылка отменена.")
            return
        if action == "broadcast_send":
            await callback.answer("Отправляю...")
            admin_id = int(callback.from_user.id)
            body = pending_broadcast_text.pop(admin_id, "").strip()
            fmt_key = pending_broadcast_format.pop(admin_id, "plain")
            with_buttons = pending_broadcast_buttons.pop(admin_id, True)
            if not body:
                await callback.message.answer("Нет текста рассылки. Сначала введите текст.")
                return
            targets = set(await repo.list_known_telegram_ids())
            if not targets:
                await callback.message.answer("Нет пользователей для рассылки.")
                return
            parse_mode = broadcast_parse_mode(fmt_key)
            ok = 0
            fail = 0
            for tg_id in targets:
                try:
                    kwargs: dict[str, Any] = {}
                    if parse_mode:
                        kwargs["parse_mode"] = parse_mode
                    if with_buttons:
                        kwargs["reply_markup"] = keyboard_for_user(
                            is_admin=is_admin(int(tg_id), settings)
                        )
                    await callback.message.bot.send_message(int(tg_id), body, **kwargs)
                    ok += 1
                except Exception:
                    fail += 1
                await asyncio.sleep(0.05)
            await callback.message.answer(f"Готово. Успешно: {ok}, ошибок: {fail}.")
            return
        if action == "ref_top":
            await callback.answer("Собираю реф-статистику...")
            try:
                text = await asyncio.wait_for(build_ref_top_text(repo, limit=10), timeout=10)
                await callback.message.answer(text)
            except asyncio.TimeoutError:
                await callback.message.answer("Реф-статистика собирается слишком долго. Попробуйте /ref_stats.")
            except Exception as exc:
                logging.exception("Ref top callback failed")
                await callback.message.answer(f"Ошибка реф-статистики: {exc}")
            return
        if action == "help":
            await callback.answer("Готово")
            providers = enabled_payment_providers(settings)
            check_hint = (
                "/check <" + "|".join(providers) + "> <payment_id>"
                if providers
                else "/check <payment_id> (оплата не настроена)"
            )
            await callback.message.answer(
                "Шпаргалка админа:\n"
                "/grant <telegram_id> <days> <gb>\n"
                "/ref_grant <telegram_id> [days]\n"
                "/grant_perm <telegram_id> [gb]\n"
                "/grant_device <telegram_id> <slot> <days> <gb>\n"
                "/sync_expire <telegram_id> [max|min|slot:<id>]\n"
                "/device_replace <telegram_id> <slot>\n"
                "/disable <telegram_id>\n"
                "/link <telegram_id> <marzban_username>\n"
                "/user <telegram_id>\n"
                "/broadcast <текст>\n"
                "/broadcast_menu\n"
                "/setenv <KEY> <VALUE>\n"
                "/deploy\n"
                "/ref_stats [telegram_id]\n"
                "/ops\n"
                f"{check_hint}\n\n"
                "Примеры:\n"
                "/grant 386029735 30 0\n"
                "/ref_grant 386029735 3\n"
                "/grant_perm 386029735 0\n"
                "/grant_device 386029735 2 30 0\n"
                "/sync_expire 386029735\n"
                "/sync_expire 386029735 min\n"
                "/sync_expire 386029735 slot:2\n"
                "/device_replace 386029735 2\n"
                "/setenv DEVICE_LIMIT 0\n"
                "/setenv PAY_RUB 149\n"
                "/setenv DEPLOY_BROADCAST_USERS 1\n"
                "/setenv CHANNEL_URL https://t.me/rootvpn_news\n"
                "/broadcast_menu\n"
                "/deploy\n"
                "/ref_stats\n"
                "/disable 386029735\n"
                "/user 386029735\n"
                "/broadcast Текст рассылки"
            )
            return
        if action == "support_templates":
            await callback.answer("Готово")
            await callback.message.answer(build_support_templates_text(), parse_mode="HTML")
            return
        await callback.answer("Неизвестное действие", show_alert=True)

    @router.message(Command("grant"))
    async def grant(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 4:
            await message.answer("Использование: /grant <telegram_id> <days> <gb>")
            return
        try:
            target = int(parts[1])
            days = int(parts[2])
            gb = int(parts[3])
        except ValueError:
            await message.answer("Ошибка формата. Пример: /grant 386029735 365 0")
            return
        if days < 0:
            await message.answer("Количество дней должно быть >= 0.")
            return
        if days == 0:
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
                logging.exception("grant: failed to read expire after perm grant for %s", target)
            logging.info("grant: perm access for %s, expire=%s", target, expire_val)
            await message.answer("Готово. Бессрочный доступ выдан.")
            await notify_access_updated(
                message.bot,
                target,
                updated,
                "Вам выдан бессрочный доступ администратором.",
                repo=repo,
                marzban=marzban,
                settings=settings,
            )
            return
        updated = await extend_access(
            telegram_id=target,
            days=days,
            gb=gb,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        await message.answer("Готово.")
        await notify_access_updated(
            message.bot,
            target,
            updated,
            "Ваш доступ продлен администратором.",
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(Command("device_add"))
    async def device_add(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) not in {2, 3}:
            await message.answer("Использование: /device_add <telegram_id> [slot]")
            return
        try:
            target = int(parts[1])
            slot = int(parts[2]) if len(parts) == 3 else 2
        except ValueError:
            await message.answer("Ошибка формата. Пример: /device_add 386029735 2")
            return
        if slot < 1:
            await message.answer("Слот должен быть >= 1")
            return
        if settings.device_limit > 0 and slot > settings.device_limit:
            await message.answer(f"Слот должен быть 1..{settings.device_limit}")
            return
        _, user, created = await ensure_device(
            telegram_id=target,
            device_id=slot,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=True,
        )
        if not user:
            await message.answer("Не удалось создать устройство.")
            return
        msg = f"Устройство {slot} создано." if created else f"Устройство {slot} уже существует."
        await message.answer(msg)

    @router.message(Command("grant_device"))
    async def grant_device(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 5:
            await message.answer("Использование: /grant_device <telegram_id> <slot> <days> <gb>")
            return
        try:
            target = int(parts[1])
            slot = int(parts[2])
            days = int(parts[3])
            gb = int(parts[4])
        except ValueError:
            await message.answer("Ошибка формата. Пример: /grant_device 386029735 2 30 0")
            return
        if slot < 1:
            await message.answer("Слот должен быть >= 1")
            return
        if settings.device_limit > 0 and slot > settings.device_limit:
            await message.answer(f"Слот должен быть 1..{settings.device_limit}")
            return
        if days < 0:
            await message.answer("Количество дней должно быть >= 0.")
            return
        if gb < 0:
            await message.answer("GB должно быть >= 0.")
            return

        try:
            updated = await extend_access_device(
                telegram_id=target,
                device_id=slot,
                days=days,
                gb=gb,
                repo=repo,
                marzban=marzban,
                settings=settings,
            )
        except Exception as exc:
            logging.exception("grant_device failed for tg=%s slot=%s", target, slot)
            await message.answer(f"Не удалось выдать доступ на устройство: {exc}")
            return

        if days == 0:
            await message.answer(f"Готово. Устройству {slot} выдан бессрочный доступ.")
            user_text = (
                f"✅ Администратор выдал бессрочный доступ для устройства {slot}."
            )
        else:
            await message.answer(f"Готово. Устройство {slot} продлено на {days} дн.")
            user_text = (
                f"✅ Администратор продлил доступ для устройства {slot} на {days} дн."
            )

        try:
            await message.bot.send_message(target, user_text)
            if slot == 1:
                await send_status_to_bot(message.bot, target, updated)
            await send_device_links_to_bot(
                bot=message.bot,
                telegram_id=target,
                repo=repo,
                marzban=marzban,
                settings=settings,
            )
        except Exception:
            logging.exception("grant_device: failed to notify user %s", target)
            await message.answer("Доступ выдан, но не удалось отправить уведомление пользователю.")

    @router.message(Command("sync_expire"))
    async def sync_expire_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) not in {2, 3}:
            await message.answer("Использование: /sync_expire <telegram_id> [max|min|slot:<id>]")
            return
        try:
            target = int(parts[1])
        except ValueError:
            await message.answer("Ошибка формата. Пример: /sync_expire 386029735")
            return

        mode = "max"
        source_slot: int | None = None
        mode_label = "максимальному сроку"
        if len(parts) == 3:
            raw_mode = parts[2].strip().lower()
            if raw_mode == "max":
                mode = "max"
                mode_label = "максимальному сроку"
            elif raw_mode == "min":
                mode = "min"
                mode_label = "минимальному сроку"
            elif raw_mode.startswith("slot:"):
                try:
                    source_slot = int(raw_mode.split(":", 1)[1])
                except ValueError:
                    await message.answer("Неверный формат слота. Пример: slot:2")
                    return
                if source_slot < 1:
                    await message.answer("Слот должен быть >= 1")
                    return
                mode = "slot"
                mode_label = f"сроку слота {source_slot}"
            else:
                await message.answer("Режим: max, min или slot:<id>")
                return

        try:
            target_expire, changed, found_count, missing_count = await sync_expire_across_devices(
                telegram_id=target,
                repo=repo,
                marzban=marzban,
                mode=mode,
                source_slot=source_slot,
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        except Exception as exc:
            logging.exception("sync_expire failed for tg=%s", target)
            await message.answer(f"Не удалось синхронизировать сроки: {exc}")
            return

        if found_count == 0:
            await message.answer("Не найдено активных профилей пользователя в Marzban.")
            return

        await message.answer(
            "Готово.\n"
            f"Режим: по {mode_label}\n"
            f"Целевой срок: {format_expire(target_expire)}\n"
            f"Изменено профилей: {changed}/{found_count}\n"
            f"Не найдено в Marzban: {missing_count}"
        )

        try:
            await message.bot.send_message(
                target,
                "✅ Администратор синхронизировал срок всех ваших устройств.\n"
                f"Новый общий срок: {format_expire(target_expire)}",
            )
            primary_row = await repo.get_user(target)
            if primary_row:
                primary = await marzban.get_user(str(primary_row["marzban_username"]))
                if primary:
                    await send_status_to_bot(message.bot, target, primary)
            await send_device_links_to_bot(
                bot=message.bot,
                telegram_id=target,
                repo=repo,
                marzban=marzban,
                settings=settings,
            )
        except Exception:
            logging.exception("sync_expire: failed to notify user %s", target)
            await message.answer("Сроки синхронизированы, но не удалось отправить уведомление пользователю.")

    @router.message(Command("device_replace"))
    async def device_replace_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            await message.answer("Использование: /device_replace <telegram_id> <slot>")
            return
        try:
            target = int(parts[1])
            slot = int(parts[2])
        except ValueError:
            await message.answer("Ошибка формата. Пример: /device_replace 386029735 2")
            return
        if slot < 1:
            await message.answer("Слот должен быть >= 1")
            return
        if settings.device_limit > 0 and slot > settings.device_limit:
            await message.answer(f"Слот должен быть 1..{settings.device_limit}")
            return
        try:
            old_username, new_username, new_user = await replace_device_slot(
                telegram_id=target,
                slot=slot,
            )
        except Exception as exc:
            logging.exception("device_replace failed for tg=%s slot=%s", target, slot)
            await message.answer(f"Не удалось заменить устройство: {exc}")
            return

        await message.answer(
            "Готово.\n"
            f"Слот: {slot}\n"
            f"Старый: {old_username}\n"
            f"Новый: {new_username}\n"
            "Старый профиль отключен."
        )
        try:
            await message.bot.send_message(
                target,
                f"🔁 Мы переиздали конфиг для устройства {slot}.\n"
                "Старый конфиг для этого устройства отключен.\n"
                "Важно: один конфиг = одно устройство.",
            )
            if slot == 1:
                await send_status_to_bot(message.bot, target, new_user)
            await send_device_links_to_bot(
                bot=message.bot,
                telegram_id=target,
                repo=repo,
                marzban=marzban,
                settings=settings,
            )
        except Exception:
            logging.exception("device_replace: failed to notify user %s", target)
            await message.answer("Профиль заменен, но не удалось отправить уведомление пользователю.")

    @router.message(Command("setenv"))
    async def setenv_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Использование: /setenv <KEY> <VALUE>")
            return
        key = parts[1].strip().upper()
        if key not in ENV_EDITABLE_KEYS:
            await message.answer(
                "Недоступный ключ. Разрешены:\n" + ", ".join(sorted(ENV_EDITABLE_KEYS.keys()))
            )
            return
        kind = ENV_EDITABLE_KEYS[key]
        value = coerce_env_value(parts[2], kind)
        if value is None:
            await message.answer(f"Неверный формат для {key} ({kind}).")
            return
        env_path = Path("/opt/vpn-bot/.env")
        try:
            update_env_file(env_path, key, value)
        except Exception as exc:
            logging.exception("setenv failed for %s", key)
            await message.answer(f"Не удалось обновить .env: {exc}")
            return
        await message.answer(f"✅ {key} обновлён на {value}. Перезапускаю vpn-bot.")
        try:
            subprocess.Popen(["systemctl", "restart", "vpn-bot"])
        except Exception:
            logging.exception("Failed to restart vpn-bot after setenv")

    @router.message(Command("deploy"))
    async def deploy_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        script = Path("/usr/local/sbin/vpn-ops-deploy")
        if not script.exists():
            await message.answer("Скрипт /usr/local/sbin/vpn-ops-deploy не найден.")
            return
        await message.answer("🚀 Запускаю deploy...")
        if start_deploy(script):
            await message.answer("Deploy запущен. Результат пришлю после перезапуска.")
            asyncio.create_task(schedule_deploy_report(message.bot))
        else:
            await message.answer("Не удалось запустить deploy.")

    @router.message(Command("broadcast_menu"))
    async def broadcast_menu_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        await message.answer("Запускаю принудительное обновление кнопок...")
        try:
            sent, total, failed, fail_samples = await broadcast_menu_update(
                bot=message.bot,
                settings=settings,
                repo=repo,
                force=True,
            )
        except Exception as exc:
            logging.exception("broadcast_menu command failed")
            await message.answer(f"Не удалось обновить кнопки: {exc}")
            return
        lines = [f"Готово. Доставлено {sent}/{total}, ошибок {failed}."]
        if fail_samples:
            lines.append("Примеры ID с ошибкой: " + ", ".join(fail_samples))
        await message.answer("\n".join(lines))

    @router.message(Command("admin_stats"))
    async def admin_stats(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        try:
            await message.answer(await asyncio.wait_for(build_admin_stats_text(repo, marzban), timeout=30))
        except asyncio.TimeoutError:
            await message.answer("Слишком долго считаю статистику. Повторите через минуту.")
        except Exception as exc:
            logging.exception("Admin stats command failed")
            await message.answer(f"Ошибка статистики: {exc}")

    @router.message(Command("ref_stats"))
    async def ref_stats_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        try:
            if len(parts) == 2:
                target_id = int(parts[1])
                stats = await repo.get_referral_stats(target_id)
                top_text = (
                    f"Рефералка для tg:{target_id}:\n"
                    f"- Приглашено: {stats['total']}\n"
                    f"- Бонус выдан: {stats['rewarded']}\n"
                    f"- Ожидают первую оплату: {stats['pending']}"
                )
                await message.answer(top_text)
                return
            text = await asyncio.wait_for(build_ref_top_text(repo, limit=10), timeout=10)
            await message.answer(text)
        except ValueError:
            await message.answer("Использование: /ref_stats [telegram_id]")
        except asyncio.TimeoutError:
            await message.answer("Реф-статистика собирается слишком долго. Повторите через минуту.")
        except Exception as exc:
            logging.exception("Ref stats command failed")
            await message.answer(f"Ошибка реф-статистики: {exc}")

    @router.message(Command("ops"))
    async def ops_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        try:
            await message.answer(
                await asyncio.wait_for(
                    build_ops_report_text(settings, marzban, sar_seconds=10),
                    timeout=25,
                )
            )
        except asyncio.TimeoutError:
            await message.answer("Ops-отчет собирается слишком долго. Повторите через минуту.")
        except Exception as exc:
            logging.exception("Ops command failed")
            await message.answer(f"Ошибка ops-отчета: {exc}")

    @router.message(Command("ref_grant"))
    async def ref_grant_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) not in {2, 3}:
            await message.answer("Использование: /ref_grant <telegram_id> [days]")
            return
        try:
            target = int(parts[1])
            days = int(parts[2]) if len(parts) == 3 else max(1, settings.referral_bonus_days)
        except ValueError:
            await message.answer("Использование: /ref_grant <telegram_id> [days]")
            return
        if days <= 0:
            await message.answer("Количество дней должно быть больше 0.")
            return
        updated = await extend_access_days_only(
            telegram_id=target,
            days=days,
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        await message.answer(f"Ручной реф-бонус выдан: tg:{target}, +{days} дн.")
        await notify_access_updated(
            message.bot,
            target,
            updated,
            f"🎁 Вам выдан реферальный бонус вручную: +{days} дн.",
            repo=repo,
            marzban=marzban,
            settings=settings,
        )

    @router.message(Command("disable"))
    async def disable(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2:
            await message.answer("Использование: /disable <telegram_id>")
            return
        row = await repo.get_user(int(parts[1]))
        if not row:
            await message.answer("Пользователь не найден.")
            return
        await marzban.modify_user(row["marzban_username"], {"status": "disabled"})
        await message.answer("Отключено.")

    @router.message(Command("link"))
    async def link(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        parts = (message.text or "").split()
        if len(parts) != 3:
            await message.answer("Использование: /link <telegram_id> <marzban_username>")
            return
        tg_id = int(parts[1])
        username = parts[2]
        user = await marzban.get_user(username)
        if not user:
            await message.answer("Пользователь Marzban не найден.")
            return
        await repo.upsert_user(tg_id, username)
        await message.answer("Привязка сохранена.")

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

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        worker_task.cancel()
        yookassa_task.cancel()
        report_task.cancel()
        deploy_report_task.cancel()
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
        await marzban.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
