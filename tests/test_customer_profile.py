from __future__ import annotations

from typing import Any

import pytest_asyncio

from src.vpnbot.bot_formatters import format_expire, format_last_online, format_limit, format_used
from src.vpnbot.customer_profile import (
    ChatIdentity,
    CustomerProfileFormatters,
    build_customer_profile_text,
)
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.device_utils import _device_label


@pytest_asyncio.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


class FakeMarzban:
    def __init__(self, users: dict[str, dict[str, Any]] | None = None):
        self.users = users or {}

    async def get_user(self, username: str) -> dict[str, Any] | None:
        return self.users.get(username)


def _fmt() -> CustomerProfileFormatters:
    return CustomerProfileFormatters(
        build_username=lambda tg_id: f"tg_{tg_id}",
        format_expire=format_expire,
        format_limit=format_limit,
        format_used=format_used,
        format_last_online=format_last_online,
        device_label=_device_label,
    )


async def test_customer_profile_combines_devices_payments_and_web_orders(repo) -> None:
    tg_id = 7007
    await repo.upsert_user(tg_id, "tg_7007")
    await repo.upsert_device(tg_id, 2, "tg_7007_d2", "Laptop")
    await repo.upsert_payment(
        provider="card",
        external_id="pay-7007",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/7007",
        status="paid_applied",
        purpose="plan_device",
        device_slot=2,
    )
    await repo.create_web_order(
        order_id="ord-7007",
        provider="card",
        external_id="web-pay-7007",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99.0,
        customer_contact="client@example.com",
        pay_url="https://pay.local/web-7007",
    )
    await repo.attach_web_order_access(order_id="ord-7007", marzban_username="tg_7007_d2")
    marzban = FakeMarzban(
        {
            "tg_7007": {"status": "active", "expire": 0, "data_limit": 0, "used_traffic": 0},
            "tg_7007_d2": {"status": "active", "expire": 0, "data_limit": 0, "used_traffic": 123},
        }
    )

    text = await build_customer_profile_text(
        telegram_id=tg_id,
        repo=repo,
        marzban=marzban,
        fmt=_fmt(),
        chat=ChatIdentity(first_name="Test", last_name="User", username="test_user"),
    )

    assert "ID 7007" in text
    assert "Test User" in text
    assert "@test_user" in text
    assert "tg_7007_d2" in text
    assert "pay-7007" in text
    assert "ord-7007" in text


async def test_customer_profile_warns_about_missing_marzban_device(repo) -> None:
    tg_id = 8008
    await repo.upsert_user(tg_id, "tg_8008")
    await repo.upsert_device(tg_id, 2, "tg_8008_d2")
    marzban = FakeMarzban(
        {
            "tg_8008": {"status": "active", "expire": 0, "data_limit": 0, "used_traffic": 0},
        }
    )

    text = await build_customer_profile_text(
        telegram_id=tg_id,
        repo=repo,
        marzban=marzban,
        fmt=_fmt(),
        chat=None,
    )

    assert "missing_in_marzban" in text
    assert "⚠️ Внимание" in text
    assert "tg_8008_d2" in text


async def test_customer_profile_handles_absent_customer(repo) -> None:
    text = await build_customer_profile_text(
        telegram_id=9009,
        repo=repo,
        marzban=FakeMarzban(),
        fmt=_fmt(),
        chat=ChatIdentity(),
    )

    assert "Primary: не найден" in text
    assert "нет primary-профиля" in text
