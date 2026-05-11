from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.vpnbot.background_tasks import spawn as _spawn_bg
from src.vpnbot.marzban_sync import audit_marzban_sync
from src.vpnbot.message_utils import split_message


@dataclass
class AdminMessageDeps:
    settings: Any
    repo: Any
    marzban: Any
    guard_message_rate_limit: Any
    is_admin_fn: Any
    extend_access_all_devices: Any
    build_username: Any
    notify_access_updated: Any
    extend_access: Any
    ensure_device: Any
    extend_access_device: Any
    send_status_to_bot: Any
    send_device_links_to_bot: Any
    sync_expire_across_devices: Any
    format_expire: Any
    replace_device_slot: Any
    env_editable_keys: dict[str, str]
    coerce_env_value: Any
    update_env_file: Any
    start_deploy: Any
    schedule_deploy_report: Any
    broadcast_menu_update: Any
    build_admin_stats_text: Any
    build_ref_top_text: Any
    build_ops_report_text: Any
    extend_access_days_only: Any


def register_admin_message_handlers(*, router: Router, deps: AdminMessageDeps) -> None:
    settings = deps.settings
    repo = deps.repo
    marzban = deps.marzban
    guard_message_rate_limit = deps.guard_message_rate_limit
    is_admin_fn = deps.is_admin_fn
    extend_access_all_devices = deps.extend_access_all_devices
    build_username = deps.build_username
    notify_access_updated = deps.notify_access_updated
    extend_access = deps.extend_access
    ensure_device = deps.ensure_device
    extend_access_device = deps.extend_access_device
    send_status_to_bot = deps.send_status_to_bot
    send_device_links_to_bot = deps.send_device_links_to_bot
    sync_expire_across_devices = deps.sync_expire_across_devices
    format_expire = deps.format_expire
    replace_device_slot = deps.replace_device_slot
    ENV_EDITABLE_KEYS = deps.env_editable_keys
    coerce_env_value = deps.coerce_env_value
    update_env_file = deps.update_env_file
    start_deploy = deps.start_deploy
    schedule_deploy_report = deps.schedule_deploy_report
    broadcast_menu_update = deps.broadcast_menu_update
    build_admin_stats_text = deps.build_admin_stats_text
    build_ref_top_text = deps.build_ref_top_text
    build_ops_report_text = deps.build_ops_report_text
    extend_access_days_only = deps.extend_access_days_only
    @router.message(Command("grant"))
    async def grant(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
                f"🔁 Мы перевыпустили ссылку для устройства {slot}.\n"
                "Старая ссылка для этого устройства отключена.\n"
                "Важно: одна ссылка = одно устройство.",
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        script = Path("/usr/local/sbin/vpn-ops-deploy")
        if not script.exists():
            await message.answer("Скрипт /usr/local/sbin/vpn-ops-deploy не найден.")
            return
        await message.answer("🚀 Запускаю deploy...")
        if start_deploy(script):
            await message.answer("Deploy запущен. Результат пришлю после перезапуска.")
            _spawn_bg(schedule_deploy_report(message.bot), name="schedule_deploy_report")
        else:
            await message.answer("Не удалось запустить deploy.")

    @router.message(Command("broadcast_menu"))
    async def broadcast_menu_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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

    @router.message(Command("sync_audit"))
    async def sync_audit_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
            await message.answer("Недостаточно прав.")
            return
        await message.answer("🧭 Проверяю Marzban/DB рассинхрон...")
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
            await message.answer("Аудит Marzban/DB занял слишком много времени. Попробуйте позже.")
            return
        except Exception as exc:
            logging.exception("Marzban sync audit command failed")
            await message.answer(f"Ошибка аудита Marzban/DB: {exc}")
            return

        text = report.summary_text(
            show=max(1, int(settings.marzban_sync_audit_show)),
            include_noncritical=True,
        )
        for chunk in split_message(text, limit=3500):
            await message.answer(chunk)

    @router.message(Command("ref_grant"))
    async def ref_grant_cmd(message: Message) -> None:
        if not await guard_message_rate_limit(message):
            return
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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
        if not message.from_user or not is_admin_fn(int(message.from_user.id), settings):
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



