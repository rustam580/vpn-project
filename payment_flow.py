import logging
import time
from typing import Any, Awaitable, Callable


CheckerFn = Callable[[Any, str], Awaitable[str]]
ApplyFn = Callable[..., Awaitable[tuple[dict[str, Any], str, str | None]]]


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
    extend_access_all_devices_fn: Callable[..., Awaitable[dict[str, Any]]],
    apply_referral_bonus_if_needed_fn: Callable[..., Awaitable[None]],
    notify_admin_payment_fn: Callable[..., Awaitable[None]] | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    purpose = str(payment.get("purpose") or "plan")
    if purpose == "device_add":
        slot = int(payment.get("device_slot") or 0)
        if strict_device_slot and (slot <= 0 or (settings.device_limit > 0 and slot > settings.device_limit)):
            await repo.set_payment_status(provider, external_id, "failed")
            return {}, purpose, "❌ Некорректный слот устройства."
        if slot > 0:
            _, updated_user, _ = await ensure_device_fn(
                telegram_id=int(payment["telegram_id"]),
                device_id=slot,
                repo=repo,
                marzban=marzban,
                settings=settings,
                create_if_missing=True,
            )
            updated = updated_user or {}
        else:
            updated = {}
    else:
        updated = await extend_access_all_devices_fn(
            telegram_id=int(payment["telegram_id"]),
            days=int(payment["days"]),
            gb=int(payment["gb"]),
            repo=repo,
            marzban=marzban,
            settings=settings,
        )
        try:
            await apply_referral_bonus_if_needed_fn(
                paid_telegram_id=int(payment["telegram_id"]),
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
    except Exception:
        await repo.set_payment_status(provider, external_id, status)
        raise

    if purpose == "device_add":
        slot = int(payment.get("device_slot") or 0)
        return (
            f"✅ Устройство {slot} добавлено.\n"
            f"Назовите его командой: /device_name {slot} Мой ноутбук",
            updated,
        )
    return "✅ Оплата подтверждена, доступ продлен.", updated
