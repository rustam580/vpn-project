"""Deploy-time reporting and post-deploy menu broadcasts.

`vpn-ops-deploy` writes a log to `DEPLOY_REPORT_PATH`; `deploy_report_worker`
polls for it, parses, formats a summary, and forwards to all admins. On a
successful deploy with `DEPLOY_BROADCAST_USERS=true`, it also pushes a
"menu refreshed" notification to every known user via `broadcast_menu_update`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot

from config import Settings
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.keyboards.bot_keyboards import keyboard_for_user
from src.vpnbot.permissions import is_admin

DEPLOY_REPORT_PATH = Path("/opt/vpn-bot/deploy/last-deploy.log")
DEPLOY_REPORT_TTL_SEC = 3600


async def broadcast_menu_update(
    *,
    bot: Bot,
    settings: Settings,
    repo: Repo,
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


async def send_deploy_report_if_any(
    bot: Bot, settings: Settings, repo: "Repo | None" = None
) -> None:
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
