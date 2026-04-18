import asyncio
import logging
import time
from typing import Any, Awaitable, Callable


CheckerFn = Callable[[Any, str], Awaitable[str]]
ApplyFn = Callable[..., Awaitable[tuple[dict[str, Any], str, str | None]]]
BYTES_IN_GB = 1024**3


class MarzbanUnavailableError(RuntimeError):
    pass


async def _run_with_marzban_retry(
    *,
    operation: str,
    telegram_id: int,
    order_id: str,
    call: Callable[[], Awaitable[Any]],
) -> Any:
    delay_sec = 1.0
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return await call()
        except Exception as exc:
            last_error = exc
            logging.exception(
                "Payment apply failed: operation=%s, telegram_id=%s, order_id=%s, attempt=%s/3",
                operation,
                telegram_id,
                order_id,
                attempt,
            )
            if attempt >= 3:
                break
            await asyncio.sleep(delay_sec)
            delay_sec *= 2
    raise MarzbanUnavailableError(
        f"marzban unavailable for operation={operation}, telegram_id={telegram_id}, order_id={order_id}"
    ) from last_error


async def apply_paid_payment(
    *,
    provider: str,
    external_id: str,
    payment: dict[str, Any],
    repo: Any,
    marzban: Any,
    settings: Any,
    bot: Any | None,
    strict_device_slot: bool,
    ensure_device_fn: Callable[..., Awaitable[tuple[str | None, dict[str, Any] | None, bool]]],
    extend_access_device_fn: Callable[..., Awaitable[dict[str, Any]]],
    extend_access_all_devices_fn: Callable[..., Awaitable[dict[str, Any]]],
    apply_referral_bonus_if_needed_fn: Callable[..., Awaitable[None]],
    notify_admin_payment_fn: Callable[..., Awaitable[None]] | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    purpose = str(payment.get("purpose") or "plan")
    order_id = str(payment.get("order_id") or external_id)
    payment_telegram_id = int(payment["telegram_id"])
    if purpose == "device_add":
        days = int(payment.get("days") or 0)
        gb = int(payment.get("gb") or 0)
        slot = int(payment.get("device_slot") or 0)
        if strict_device_slot and (slot <= 0 or (settings.device_limit > 0 and slot > settings.device_limit)):
            await repo.set_payment_status(provider, external_id, "failed")
            return {}, purpose, "❌ Некорректный слот устройства."
        if slot > 0:
            username, updated_user, created_new_slot = await _run_with_marzban_retry(
                operation="ensure_device",
                telegram_id=payment_telegram_id,
                order_id=order_id,
                call=lambda: ensure_device_fn(
                    telegram_id=payment_telegram_id,
                    device_id=slot,
                    repo=repo,
                    marzban=marzban,
                    settings=settings,
                    create_if_missing=True,
                ),
            )
            if not username:
                await repo.set_payment_status(provider, external_id, "failed")
                return {}, purpose, "❌ Не удалось создать устройство."
            updated = updated_user or {}
            if days > 0 or gb != 0:
                now = int(time.time())
                current_expire = int((updated.get("expire") or 0))
                if days > 0:
                    # For a newly purchased additional slot we start its own term from "now",
                    # even if Marzban created it by copying the primary profile's longer expiry.
                    expire_base = now if created_new_slot else max(now, current_expire)
                    target_expire = expire_base + days * 24 * 3600
                else:
                    target_expire = current_expire

                current_limit = int((updated.get("data_limit") or 0))
                if gb <= 0:
                    target_limit = 0
                else:
                    base_limit = gb * BYTES_IN_GB
                    target_limit = max(current_limit, base_limit) if current_limit > 0 else base_limit

                updated = await _run_with_marzban_retry(
                    operation="modify_user",
                    telegram_id=payment_telegram_id,
                    order_id=order_id,
                    call=lambda: marzban.modify_user(
                        username,
                        {
                            "expire": target_expire,
                            "data_limit": target_limit,
                            "status": "active",
                        },
                    ),
                )
        else:
            updated = {}
    elif purpose == "plan_device":
        slot = int(payment.get("device_slot") or 0)
        if slot <= 0 or (settings.device_limit > 0 and slot > settings.device_limit):
            await repo.set_payment_status(provider, external_id, "failed")
            return {}, purpose, "❌ Некорректный слот устройства."
        updated = await _run_with_marzban_retry(
            operation="extend_access_device",
            telegram_id=payment_telegram_id,
            order_id=order_id,
            call=lambda: extend_access_device_fn(
                telegram_id=payment_telegram_id,
                device_id=slot,
                days=int(payment["days"]),
                gb=int(payment["gb"]),
                repo=repo,
                marzban=marzban,
                settings=settings,
            ),
        )
    else:
        updated = await _run_with_marzban_retry(
            operation="extend_access_all_devices",
            telegram_id=payment_telegram_id,
            order_id=order_id,
            call=lambda: extend_access_all_devices_fn(
                telegram_id=payment_telegram_id,
                days=int(payment["days"]),
                gb=int(payment["gb"]),
                repo=repo,
                marzban=marzban,
                settings=settings,
            ),
        )
        try:
            await apply_referral_bonus_if_needed_fn(
                paid_telegram_id=payment_telegram_id,
                repo=repo,
                marzban=marzban,
                settings=settings,
                bot=bot,
            )
        except Exception:
            logging.exception("Referral bonus apply failed for user %s", payment["telegram_id"])

    await repo.set_payment_status(provider, external_id, "paid_applied")
    try:
        await repo.log_event(
            event_type=("payment_paid_device" if purpose == "device_add" else "payment_paid_plan"),
            telegram_id=int(payment["telegram_id"]),
            event_value=provider,
            event_meta={
                "external_id": external_id,
                "purpose": purpose,
                "device_slot": int(payment.get("device_slot") or 0),
            },
        )
    except Exception:
        logging.exception("Payment event track failed for %s", external_id)

    if bot is not None and notify_admin_payment_fn is not None:
        try:
            await notify_admin_payment_fn(
                bot=bot,
                settings=settings,
                repo=repo,
                payment=payment,
            )
        except Exception:
            logging.exception("Payment notify: failed after apply for %s", external_id)

    return updated, purpose, None


async def _resolve_provider_status(
    *,
    provider: str,
    external_id: str,
    settings: Any,
    cryptobot_check_invoice_fn: CheckerFn,
    yookassa_check_payment_fn: CheckerFn,
) -> tuple[str, bool]:
    if provider == "crypto":
        status = await cryptobot_check_invoice_fn(settings, external_id)
        return status, status == "paid"
    if provider == "card":
        status = await yookassa_check_payment_fn(settings, external_id)
        return status, status == "succeeded"
    raise ValueError("unknown_provider")


async def check_and_apply_payment(
    *,
    provider: str,
    external_id: str,
    telegram_id: int,
    repo: Any,
    marzban: Any,
    settings: Any,
    bot: Any | None = None,
    cryptobot_check_invoice_fn: CheckerFn,
    yookassa_check_payment_fn: CheckerFn,
    apply_paid_payment_fn: ApplyFn,
) -> tuple[str, dict[str, Any] | None]:
    payment = await repo.get_payment(provider, external_id)
    if not payment:
        return "❌ Платеж не найден.", None
    if int(payment["telegram_id"]) != telegram_id:
        return "❌ Этот платеж создан для другого пользователя.", None
    if payment["status"] == "paid_applied":
        return "✅ Этот платеж уже обработан.", None

    try:
        status, paid = await _resolve_provider_status(
            provider=provider,
            external_id=external_id,
            settings=settings,
            cryptobot_check_invoice_fn=cryptobot_check_invoice_fn,
            yookassa_check_payment_fn=yookassa_check_payment_fn,
        )
    except ValueError:
        return "❌ Неизвестный провайдер.", None

    if not paid:
        await repo.set_payment_status(provider, external_id, status)
        return f"⏳ Платеж еще не подтвержден (статус: {status}).", None

    claimed = await repo.claim_payment_for_apply(provider, external_id)
    if not claimed:
        latest = await repo.get_payment(provider, external_id)
        if latest and latest.get("status") == "paid_applied":
            return "✅ Этот платеж уже обработан.", None
        if latest and latest.get("status") == "processing":
            updated_at = int(latest.get("updated_at") or 0)
            age = int(time.time()) - updated_at if updated_at > 0 else 0
            if age >= settings.payment_processing_requeue_seconds:
                await repo.set_payment_status(provider, external_id, "pending")
                claimed = await repo.claim_payment_for_apply(provider, external_id)
                if not claimed:
                    return "⏳ Платеж был перезапущен. Нажмите «Проверить оплату» еще раз.", None
            else:
                return "⏳ Платеж обрабатывается, подождите 5-10 секунд.", None
        if not claimed:
            return "⏳ Платеж обрабатывается, подождите 5-10 секунд.", None

    try:
        updated, purpose, error = await apply_paid_payment_fn(
            provider=provider,
            external_id=external_id,
            payment=payment,
            repo=repo,
            marzban=marzban,
            settings=settings,
            bot=bot,
            strict_device_slot=True,
        )
        if error:
            return error, None
    except MarzbanUnavailableError:
        await repo.set_payment_status(provider, external_id, "processing")
        return "⏳ Платеж подтвержден, но выдача доступа еще в обработке. Повторите проверку через 10-20 секунд.", None
    except Exception:
        await repo.set_payment_status(provider, external_id, status)
        raise

    if purpose == "device_add":
        slot = int(payment.get("device_slot") or 0)
        added_days = int(payment.get("days") or 0)
        if added_days > 0:
            return (
                f"✅ Устройство {slot} добавлено, срок нового устройства: +{added_days} дней.\n"
                f"Назовите его командой: /device_name {slot} Мой ноутбук",
                updated,
            )
        return (
            f"✅ Устройство {slot} добавлено.\n"
            f"Назовите его командой: /device_name {slot} Мой ноутбук",
            updated,
        )
    if purpose == "plan_device":
        slot = int(payment.get("device_slot") or 0)
        return f"✅ Оплата подтверждена. Доступ для устройства {slot} продлен.", updated
    if purpose == "plan_all":
        return "✅ Оплата подтверждена, доступ продлен для всех устройств.", updated
    return "✅ Оплата подтверждена, доступ продлен.", updated
