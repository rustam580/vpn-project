from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.vpnbot.background_tasks import spawn as _spawn_bg
from src.vpnbot.drift_resolver import (
    drop_missing_marzban_db_ref,
    ignore_drift,
    recreate_missing_marzban_user,
    retry_web_order_access,
)
from src.vpnbot.keyboards.drift_keyboards import (
    ACTION_DROP_DB_REF,
    ACTION_IGNORE,
    ACTION_RECREATE,
    ACTION_RETRY_WEB_ORDER,
    drift_finding_keyboard,
    parse_drift_callback,
)
from src.vpnbot.keyboards.web_order_keyboards import (
    ACTION_CHECK_PAYMENT,
    parse_web_order_callback,
)
from src.vpnbot.marzban_sync import audit_marzban_sync
from src.vpnbot.message_utils import split_message
from src.vpnbot.olcrtc_rescue import fetch_rescue_status, validate_session_id
from src.vpnbot.payment_helpers import cryptobot_check_invoice, yookassa_check_payment
from src.vpnbot.payment_issues import build_payment_issues_report
from src.vpnbot.xray_quality import format_xray_quality_report, summarize_xray_error_log


@dataclass
class AdminCallbackDeps:
    settings: Any
    repo: Any
    marzban: Any
    guard_callback_rate_limit: Any
    is_admin_fn: Any
    admin_panel_keyboard: Any
    admin_plans_text: Any
    admin_plans_keyboard: Any
    preset_plans: Any
    plans_to_json: Any
    update_env_file: Any
    plan_title: Any
    plan_gb_text: Any
    build_admin_stats_text: Any
    build_ops_report_text: Any
    start_deploy: Any
    schedule_deploy_report: Any
    pending_user_lookup: set[int]
    pending_device_add_prompt: set[int]
    pending_broadcast_prompt: set[int]
    pending_broadcast_format: dict[int, str]
    pending_broadcast_buttons: dict[int, bool]
    pending_broadcast_text: dict[int, str]
    broadcast_next_format: Any
    send_broadcast_preview: Any
    broadcast_parse_mode: Any
    keyboard_for_user: Any
    build_ref_top_text: Any
    enabled_payment_providers: Any
    build_support_templates_text: Any


def register_admin_callback_handlers(*, router: Router, deps: AdminCallbackDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_callback_rate_limit = deps.guard_callback_rate_limit
    is_admin_fn = deps.is_admin_fn
    admin_panel_keyboard = deps.admin_panel_keyboard
    admin_plans_text = deps.admin_plans_text
    admin_plans_keyboard = deps.admin_plans_keyboard
    preset_plans = deps.preset_plans
    plans_to_json = deps.plans_to_json
    update_env_file = deps.update_env_file
    plan_title = deps.plan_title
    plan_gb_text = deps.plan_gb_text
    build_admin_stats_text = deps.build_admin_stats_text
    build_ops_report_text = deps.build_ops_report_text
    start_deploy = deps.start_deploy
    schedule_deploy_report = deps.schedule_deploy_report
    pending_user_lookup = deps.pending_user_lookup
    pending_device_add_prompt = deps.pending_device_add_prompt
    pending_broadcast_prompt = deps.pending_broadcast_prompt
    pending_broadcast_format = deps.pending_broadcast_format
    pending_broadcast_buttons = deps.pending_broadcast_buttons
    pending_broadcast_text = deps.pending_broadcast_text
    broadcast_next_format = deps.broadcast_next_format
    send_broadcast_preview = deps.send_broadcast_preview
    broadcast_parse_mode = deps.broadcast_parse_mode
    keyboard_for_user = deps.keyboard_for_user
    build_ref_top_text = deps.build_ref_top_text
    enabled_payment_providers = deps.enabled_payment_providers
    build_support_templates_text = deps.build_support_templates_text

    @router.callback_query(F.data.startswith("admin:"))
    async def admin_callback(callback: CallbackQuery) -> None:
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        if not is_admin_fn(int(callback.from_user.id), settings):
            await callback.answer("Недостаточно прав.", show_alert=True)
            return
        action = callback.data.split(":", 1)[1]
        if action == "home":
            await callback.answer("Готово")
            await callback.message.answer(
                "Админ-кабинет:\n"
                "- Статистика по пользователям и платежам\n"
                "- Быстрые действия без ручного ввода команд",
                reply_markup=admin_panel_keyboard(),
            )
            return
        if action == "plans":
            await callback.answer("Готово")
            await callback.message.answer(
                "💼 Управление тарифами\n\n"
                + admin_plans_text(settings)
                + "\n\n"
                "Выберите готовый пресет или покажите команду для ручной настройки.",
                reply_markup=admin_plans_keyboard(),
            )
            return
        if action == "plans:manual":
            await callback.answer("Готово")
            await callback.message.answer(
                "Ручная настройка тарифов:\n"
                "<code>/setenv PLANS_JSON [{\"key\":\"m1\",\"title\":\"1 месяц\",\"days\":30,\"gb\":0,\"rub\":99},"
                "{\"key\":\"m3\",\"title\":\"3 месяца\",\"days\":90,\"gb\":0,\"rub\":259},"
                "{\"key\":\"y1\",\"title\":\"12 месяцев\",\"days\":365,\"gb\":0,\"rub\":949}]</code>\n\n"
                "После применения бот перезапустится автоматически.",
                parse_mode="HTML",
                reply_markup=admin_plans_keyboard(),
            )
            return
        if action.startswith("plans:set:"):
            preset_key = action.split(":", 2)[2].strip()
            plans = preset_plans(preset_key)
            if not plans:
                await callback.answer("Неизвестный пресет", show_alert=True)
                return
            env_path = Path("/opt/vpn-bot/.env")
            json_value = plans_to_json(plans)
            try:
                update_env_file(env_path, "PLANS_JSON", json_value)
            except Exception as exc:
                logging.exception("Failed to apply plans preset: %s", preset_key)
                await callback.answer("Ошибка применения", show_alert=True)
                await callback.message.answer(f"Не удалось обновить PLANS_JSON: {exc}")
                return
            await callback.answer("Тарифы обновлены")
            lines = ["Текущие тарифы:"]
            for plan in plans:
                lines.append(
                    f"- {plan.key}: {plan_title(plan)} • {plan.rub:.2f} RUB • {plan.days} дн • {plan_gb_text(plan.gb)}"
                )
            await callback.message.answer(
                "✅ Пресет тарифов применен.\n"
                f"Профиль: {preset_key}\n"
                + "\n".join(lines)
                + "\n\nПерезапускаю vpn-bot...",
                reply_markup=admin_plans_keyboard(),
            )
            try:
                subprocess.Popen(["systemctl", "restart", "vpn-bot"])
            except Exception:
                logging.exception("Failed to restart vpn-bot after plans preset")
            return
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
        if action == "payment_issues":
            await callback.answer("Checking payments...")
            try:
                text = await asyncio.wait_for(
                    build_payment_issues_report(repo, marzban, settings),
                    timeout=60,
                )
                for chunk in split_message(text, limit=3500):
                    await callback.message.answer(chunk)
            except asyncio.TimeoutError:
                await callback.message.answer("Payment issues report timed out. Try /payment_issues.")
            except Exception as exc:
                logging.exception("Payment issues callback failed")
                await callback.message.answer(f"Payment issues report error: {exc}")
            return
        if action == "sync_audit":
            await callback.answer("Проверяю Marzban/DB...")
            try:
                report = await asyncio.wait_for(
                    audit_marzban_sync(
                        repo,
                        marzban,
                        limit=max(20, int(settings.marzban_sync_audit_limit)),
                    ),
                    timeout=90,
                )
            except asyncio.TimeoutError:
                await callback.message.answer("Аудит Marzban/DB занял слишком много времени. Попробуйте позже.")
                return
            except Exception as exc:
                logging.exception("Marzban sync audit callback failed")
                await callback.message.answer(f"Ошибка аудита Marzban/DB: {exc}")
                return
            text = report.summary_text(
                show=max(1, int(settings.marzban_sync_audit_show)),
                include_noncritical=True,
            )
            for chunk in split_message(text, limit=3500):
                await callback.message.answer(chunk)
            critical = report.critical_findings()
            if critical:
                max_cards = max(1, int(getattr(settings, "marzban_sync_audit_show", 8)))
                await callback.message.answer(
                    f"🔧 Действия по drift (показываю {min(len(critical), max_cards)} из {len(critical)}):"
                )
                for finding in critical[:max_cards]:
                    await callback.message.answer(
                        f"<b>{finding.kind}</b>\n<code>{finding.finding_id}</code>\n{finding.summary}",
                        parse_mode="HTML",
                        reply_markup=drift_finding_keyboard(finding),
                    )
            return
        if action == "xray_errors":
            await callback.answer("Смотрю Xray error log...")
            try:
                summary = summarize_xray_error_log(
                    settings.xray_error_log_path,
                    window_minutes=max(1, int(settings.xray_quality_monitor_window_min)),
                )
            except Exception as exc:
                logging.exception("Xray quality callback failed")
                await callback.message.answer(f"Ошибка чтения Xray error log: {exc}")
                return
            text = format_xray_quality_report(
                summary,
                show=max(1, int(settings.xray_quality_monitor_show)),
            )
            for chunk in split_message(text, limit=3500):
                await callback.message.answer(chunk)
            return
        if action.startswith("rescue_status:"):
            raw_session_id = action.split(":", 1)[1].strip()
            try:
                session_id = validate_session_id(raw_session_id)
            except ValueError:
                await callback.answer("Bad session id", show_alert=True)
                return
            deploy_host = str(getattr(settings, "olcrtc_rescue_deploy_host", "") or "").strip()
            if not deploy_host:
                await callback.answer("OLCRTC_RESCUE_DEPLOY_HOST is empty", show_alert=True)
                return
            await callback.answer("Checking Rescue status...")
            result = await fetch_rescue_status(
                session_id=session_id,
                deploy_host=deploy_host,
                timeout_sec=int(getattr(settings, "olcrtc_rescue_deploy_timeout_sec", 60)),
            )
            prefix = "Rescue status: ok" if result.ok else f"Rescue status: failed at {result.failed_step}"
            for chunk in split_message(f"{prefix}\n{result.output}", limit=3500):
                await callback.message.answer(chunk)
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
                _spawn_bg(schedule_deploy_report(callback.message.bot), name="schedule_deploy_report")
            else:
                await callback.message.answer("Не удалось запустить deploy.")
            return
        if action == "find_user":
            await callback.answer("Ок")
            pending_user_lookup.add(int(callback.from_user.id))
            await callback.message.answer(
                "Введите Telegram ID, order ID, email/контакт или Marzban username. Или «отмена»."
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
            await send_broadcast_preview(callback.message, body, admin_id=admin_id)
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
            await send_broadcast_preview(callback.message, body, admin_id=admin_id)
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
            targets = {int(tg_id) for tg_id in await repo.list_known_telegram_ids()}
            targets.discard(admin_id)
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
                            is_admin=is_admin_fn(int(tg_id), settings)
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
                "/user <telegram_id|order_id|email|marzban_username>\n"
                "/broadcast <текст>\n"
                "/broadcast_menu\n"
                "/setenv <KEY> <VALUE>\n"
                "/deploy\n"
                "/ref_stats [telegram_id]\n"
                "/ops\n"
                "/payment_issues\n"
                "/sync_audit\n"
                "/xray_errors [minutes]\n"
                "/rescue <telegram_id> <wb_room_url>\n"
                "/rescue_status <session_id>\n"
                "/rescue_list\n"
                "/rescue_stop <session_id>\n"
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
                "/setenv PLANS_JSON [{...}]\n"
                "/setenv DEPLOY_BROADCAST_USERS 1\n"
                "/setenv CHANNEL_URL https://t.me/rootvpn_news\n"
                "/broadcast_menu\n"
                "/deploy\n"
                "/ref_stats\n"
                "/sync_audit\n"
                "/xray_errors 15\n"
                "/rescue 386029735 https://stream.wb.ru/room/019e...\n"
                "/rescue_status rs-20260518202449-386029735\n"
                "/rescue_list\n"
                "/rescue_stop rs-20260518202449-386029735\n"
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

    @router.callback_query(F.data.startswith("wo:"))
    async def web_order_action_callback(callback: CallbackQuery) -> None:
        """Read-only support actions emitted by web-order lookup cards."""
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Callback error", show_alert=True)
            return
        if not is_admin_fn(int(callback.from_user.id), settings):
            await callback.answer("Not enough permissions.", show_alert=True)
            return

        parsed = parse_web_order_callback(callback.data)
        if parsed is None:
            await callback.answer("Bad web-order action.", show_alert=True)
            return
        action, order_id = parsed
        if action != ACTION_CHECK_PAYMENT:
            await callback.answer("Unknown web-order action.", show_alert=True)
            return

        await callback.answer("Checking provider status...")
        order = await repo.get_web_order(order_id)
        if not order:
            await callback.message.answer(f"Web order not found: {order_id}")
            return

        provider = str(order.get("provider") or "").strip()
        external_id = str(order.get("external_id") or "").strip()
        local_status = str(order.get("status") or "").strip()
        if not provider or not external_id:
            await callback.message.answer(
                f"Web order {order_id} has no provider/external_id. Local status: {local_status or 'n/a'}"
            )
            return

        try:
            if provider == "crypto":
                remote_status = await asyncio.wait_for(
                    cryptobot_check_invoice(settings, external_id),
                    timeout=30,
                )
            elif provider == "card":
                remote_status = await asyncio.wait_for(
                    yookassa_check_payment(settings, external_id),
                    timeout=30,
                )
            else:
                remote_status = f"unsupported provider: {provider}"
        except asyncio.TimeoutError:
            await callback.message.answer(
                f"Payment status check timed out.\norder={order_id}\nprovider={provider}\nexternal_id={external_id}"
            )
            return
        except Exception as exc:
            logging.exception("Web order payment status check failed: %s", order_id)
            await callback.message.answer(
                f"Payment status check failed.\n"
                f"order={order_id}\nprovider={provider}\nexternal_id={external_id}\nerror={exc}"
            )
            return

        await callback.message.answer(
            "Web order payment status (read-only):\n"
            f"- order: {order_id}\n"
            f"- provider: {provider}\n"
            f"- external_id: {external_id}\n"
            f"- local_status: {local_status or 'n/a'}\n"
            f"- provider_status: {remote_status}\n\n"
            "If provider_status is paid/succeeded but access is missing, run the drift audit button."
        )

    @router.callback_query(F.data.startswith("da:"))
    async def drift_action_callback(callback: CallbackQuery) -> None:
        """Apply one of the safe drift-resolution actions emitted by sync_audit cards."""
        if not await guard_callback_rate_limit(callback):
            return
        if not callback.data or not callback.from_user or callback.message is None:
            await callback.answer("Ошибка callback", show_alert=True)
            return
        if not is_admin_fn(int(callback.from_user.id), settings):
            await callback.answer("Недостаточно прав.", show_alert=True)
            return

        parsed = parse_drift_callback(callback.data)
        if parsed is None:
            await callback.answer("Некорректные данные действия.", show_alert=True)
            return
        finding_id, action = parsed
        actor_tg = int(callback.from_user.id)

        await callback.answer("Применяю...")
        try:
            report = await asyncio.wait_for(
                audit_marzban_sync(
                    repo,
                    marzban,
                    limit=max(20, int(settings.marzban_sync_audit_limit)),
                ),
                timeout=90,
            )
        except asyncio.TimeoutError:
            await callback.message.answer("Не удалось обновить аудит для подтверждения.")
            return
        except Exception as exc:
            logging.exception("Drift re-audit failed before resolution")
            await callback.message.answer(f"Ошибка повторного аудита: {exc}")
            return

        finding = report.find_by_id(finding_id)
        if finding is None and action != ACTION_IGNORE:
            await callback.message.answer(
                f"Drift {finding_id} больше не наблюдается. Возможно, уже разрешен."
            )
            return

        try:
            if action == ACTION_RECREATE and finding is not None:
                result = await recreate_missing_marzban_user(
                    finding, repo=repo, marzban=marzban, settings=settings, actor_tg=actor_tg
                )
            elif action == ACTION_DROP_DB_REF and finding is not None:
                result = await drop_missing_marzban_db_ref(
                    finding, repo=repo, actor_tg=actor_tg
                )
            elif action == ACTION_RETRY_WEB_ORDER and finding is not None:
                result = await retry_web_order_access(
                    finding, repo=repo, marzban=marzban, settings=settings, actor_tg=actor_tg
                )
            elif action == ACTION_IGNORE:
                # If the finding has vanished by now we still record an ignore by id,
                # which keeps the audit trail consistent.
                if finding is None:
                    from src.vpnbot.marzban_sync import DriftFinding, prefix_to_kind
                    prefix = finding_id.split(":", 1)[0] if ":" in finding_id else ""
                    finding = DriftFinding(
                        kind=prefix_to_kind(prefix) or "unknown",
                        finding_id=finding_id,
                        summary="(finding already resolved or stale)",
                        payload={},
                    )
                result = await ignore_drift(finding, repo=repo, actor_tg=actor_tg)
            else:
                await callback.message.answer(f"Неизвестное действие: {action}.")
                return
        except Exception as exc:
            logging.exception(
                "drift_action_callback failed: finding=%s action=%s", finding_id, action
            )
            await callback.message.answer(f"Ошибка применения действия: {exc}")
            return

        status_icon = "✅" if result.ok else "❌"
        await callback.message.answer(
            f"{status_icon} {result.action or action} • {result.finding_id or finding_id}\n{result.message}"
        )
