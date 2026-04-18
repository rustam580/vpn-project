from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

import bot
from src.vpnbot.services import payment_flow as payment_flow_service
from src.vpnbot.services.payment_flow import MarzbanUnavailableError


class _StubRepo:
    def __init__(self) -> None:
        self.status_updates: list[tuple[str, str, str]] = []
        self.events: list[dict] = []

    async def set_payment_status(self, provider: str, external_id: str, status: str) -> None:
        self.status_updates.append((provider, external_id, status))

    async def log_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class _StubMarzban:
    async def modify_user(self, username: str, payload: dict) -> dict:
        return {"username": username, **payload}


@pytest.mark.asyncio
async def test_apply_paid_payment_retries_marzban_calls() -> None:
    repo = _StubRepo()
    marzban = _StubMarzban()
    call_count = {"ensure_device": 0}

    async def flaky_ensure_device(**_kwargs):
        call_count["ensure_device"] += 1
        if call_count["ensure_device"] < 3:
            raise RuntimeError("marzban temporary error")
        return "tg_1_d2", {"expire": 0, "data_limit": 0}, True

    async def extend_access_device_stub(**_kwargs):
        return {}

    async def extend_access_all_stub(**_kwargs):
        return {}

    async def apply_ref_bonus_stub(**_kwargs):
        return None

    payment = {
        "telegram_id": 1,
        "purpose": "device_add",
        "days": 30,
        "gb": 0,
        "device_slot": 2,
    }
    settings = SimpleNamespace(device_limit=5)

    updated, purpose, error = await payment_flow_service.apply_paid_payment(
        provider="crypto",
        external_id="ext-1",
        payment=payment,
        repo=repo,
        marzban=marzban,
        settings=settings,
        bot=None,
        strict_device_slot=True,
        ensure_device_fn=flaky_ensure_device,
        extend_access_device_fn=extend_access_device_stub,
        extend_access_all_devices_fn=extend_access_all_stub,
        apply_referral_bonus_if_needed_fn=apply_ref_bonus_stub,
    )

    assert error is None
    assert purpose == "device_add"
    assert updated.get("status") == "active"
    assert call_count["ensure_device"] == 3


@pytest_asyncio.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = bot.Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_check_and_apply_payment_keeps_processing_on_marzban_failure(
    repo, monkeypatch
) -> None:
    tg_id = 99123
    external_id = "inv-retry-marzban-down"

    await repo.upsert_payment(
        provider="crypto",
        external_id=external_id,
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=99,
        pay_url="https://pay.local/inv-retry-marzban-down",
        status="pending",
        purpose="plan",
    )

    async def paid_status(_settings, _external_id):
        return "paid"

    async def marzban_down_apply(**_kwargs):
        raise MarzbanUnavailableError("marzban unavailable")

    monkeypatch.setattr(bot, "cryptobot_check_invoice", paid_status)
    monkeypatch.setattr(bot, "apply_paid_payment", marzban_down_apply)

    settings = SimpleNamespace(
        payment_processing_requeue_seconds=120,
        device_limit=5,
        trial_days=1,
        trial_gb=0,
    )

    result, updated = await bot.check_and_apply_payment(
        provider="crypto",
        external_id=external_id,
        telegram_id=tg_id,
        repo=repo,
        marzban=object(),
        settings=settings,
        bot=None,
    )

    assert "в обработке" in result
    assert updated is None
    payment = await repo.get_payment("crypto", external_id)
    assert payment is not None
    assert payment["status"] == "processing"
