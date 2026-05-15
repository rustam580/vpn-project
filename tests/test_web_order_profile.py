from __future__ import annotations

from typing import Any

import pytest_asyncio

from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.web_order_profile import build_web_order_profile_lines


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


async def test_web_order_profile_links_telegram_device_and_actions(repo) -> None:
    await repo.upsert_device(12345, 2, "web_order_user", "Parent phone")
    order = {
        "order_id": "ord-123",
        "provider": "card",
        "external_id": "pay-123",
        "status": "paid_applied",
        "plan_key": "m1",
        "days": 30,
        "gb": 0,
        "amount_rub": 99.0,
        "customer_contact": "client@example.com",
        "marzban_username": "web_order_user",
        "updated_at": 1_700_000_000,
    }

    lines, tg_ids = await build_web_order_profile_lines(
        order,
        repo=repo,
        marzban=FakeMarzban({"web_order_user": {"status": "active", "expire": 0}}),
    )
    text = "\n".join(lines)

    assert tg_ids == {12345}
    assert "ord-123" in text
    assert "pay-123" in text
    assert "TG <code>12345</code>" in text
    assert "/user 12345" in text
    assert "/check card pay-123" in text
    assert "/sync_audit" in text


async def test_web_order_profile_warns_when_access_not_bound(repo) -> None:
    order = {
        "order_id": "ord-empty",
        "provider": "card",
        "external_id": "pay-empty",
        "status": "paid_applied",
        "plan_key": "m1",
        "days": 30,
        "gb": 0,
        "amount_rub": 99.0,
        "customer_contact": "",
        "marzban_username": "",
        "updated_at": 0,
    }

    lines, tg_ids = await build_web_order_profile_lines(order, repo=repo, marzban=FakeMarzban())
    text = "\n".join(lines)

    assert tg_ids == set()
    assert "Marzban username: empty" in text
    assert "no Telegram link yet" in text
