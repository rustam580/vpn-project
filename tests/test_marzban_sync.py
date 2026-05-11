from __future__ import annotations

import pytest

from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.marzban_sync import audit_marzban_sync


class FakeMarzban:
    def __init__(self, users: list[dict[str, object]]):
        self.users = users

    async def req(self, method: str, path: str, **kwargs):
        assert method == "GET"
        assert path in {"/api/users", "/api/users/"}
        limit = int(kwargs.get("params", {}).get("limit", 100))
        offset = int(kwargs.get("params", {}).get("offset", 0))
        return {
            "users": self.users[offset : offset + limit],
            "total": len(self.users),
        }

    async def get_user(self, username: str):
        return next((user for user in self.users if user.get("username") == username), None)


@pytest.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


async def test_audit_marzban_sync_reports_critical_and_noncritical_findings(repo) -> None:
    await repo.upsert_user(1001, "tg_1001")
    await repo.upsert_device(1001, 2, "custom_parent_device")
    await repo.create_web_order(
        order_id="order-without-access",
        provider="card",
        external_id="pay-1",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="client@example.com",
        pay_url="https://pay.example",
    )

    marzban = FakeMarzban(
        [
            {"username": "tg_1001", "status": "active", "expire": 0},
            {"username": "web_unknown", "status": "disabled", "expire": 0},
        ]
    )

    report = await audit_marzban_sync(repo, marzban, limit=100)

    assert report.has_critical_findings() is True
    assert any("custom_parent_device" in item for item in report.missing_in_marzban)
    assert any("order-without-access" in item for item in report.web_orders_without_access)
    assert any("web_unknown" in item for item in report.unknown_in_db)
    assert any("expected=tg_1001_d2" in item for item in report.non_standard_device_names)
