from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from src.vpnbot.bot_network import measure_iface_mbps, measure_iface_mbps_sar

if TYPE_CHECKING:
    from src.vpnbot.db.bot_repo import Repo
    from src.vpnbot.services.bot_marzban import MarzbanClient


class SettingsLike(Protocol):
    net_iface: str
    port_speed_mbps: float
    port_utilization: float
    concurrency_ratio: float

def _event_users(summary: dict[str, dict[str, int]], key: str) -> int:
    item = summary.get(key) or {}
    return int(item.get("users", 0) or 0)


async def build_funnel_24h_text(repo: "Repo") -> str:
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
        f"- Получили подписку: {config_users} ({config_conv:.1f}% от стартов)\n"
        f"- Выдано триалов: {trial_users}\n"
        f"- Создали платеж (тариф): {pay_create_users}\n"
        f"- Оплатили тариф: {pay_apply_users} ({pay_conv:.1f}% от стартов, {checkout_conv:.1f}% от checkout)\n"
        f"- Жалобы на подключение: {issue_users}"
    )


async def build_admin_stats_text(repo: "Repo", marzban: "MarzbanClient") -> str:
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
    sub_adoption = await repo.subscription_adoption_stats(days=7)
    sub_pending_rows = await repo.list_subscription_non_adopters(days=7, limit=5)
    sub_pending_preview = ", ".join(
        f"tg:{int(row['telegram_id'])}" for row in sub_pending_rows
    ) or "нет"
    web_bind = await repo.web_bind_conversion_stats(days=7)

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
        "Подписки (за 7 дней):\n"
        f"- Перешли на подписку: {sub_adoption['adopted_users']}/{sub_adoption['total_users']} ({sub_adoption['adoption_pct']:.1f}%)\n"
        f"- Еще не перешли: {sub_adoption['pending_users']}\n"
        f"- Примеры без перехода: {sub_pending_preview}\n\n"
        "Сайт -> Telegram (за 7 дней):\n"
        f"- Оплачено на сайте (paid_applied): {web_bind['paid_orders']}\n"
        f"- Привязано к Telegram: {web_bind['bound_from_paid']} ({web_bind['conversion_pct']:.1f}%)\n"
        f"- Еще не привязали: {web_bind['pending_bind']}\n\n"
        f"{funnel_text}"
        f"{db_tip}"
    )


async def build_ref_top_text(repo: "Repo", limit: int = 10) -> str:
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
    settings: SettingsLike, marzban: "MarzbanClient", *, sar_seconds: int = 10
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


async def build_payments_summary(repo: "Repo") -> str:
    counts = await repo.payment_status_counts()
    if not counts:
        return "Платежи: данных нет"
    lines = ["Платежи:"]
    for status, cnt in sorted(counts.items()):
        lines.append(f"- {status}: {cnt}")
    return "\n".join(lines)

