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
            {"username": "web_unknown", "status": "active", "expire": 0},
        ]
    )

    report = await audit_marzban_sync(repo, marzban, limit=100)

    assert report.has_critical_findings() is True
    assert any("custom_parent_device" in item for item in report.missing_in_marzban)
    assert any("order-without-access" in item for item in report.web_orders_without_access)
    assert any("web_unknown" in item for item in report.unknown_in_db)
    assert any("expected=tg_1001_d2" in item for item in report.non_standard_device_names)


async def test_audit_marzban_sync_ignores_removed_web_order_access(repo) -> None:
    await repo.create_web_order(
        order_id="removed-order",
        provider="card",
        external_id="pay-removed",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="old@example.com",
        pay_url="https://pay.example",
    )
    await repo.attach_web_order_access(order_id="removed-order", marzban_username="web_removed")
    await repo.set_web_order_status("removed-order", "manual_removed")

    report = await audit_marzban_sync(repo, FakeMarzban([]), limit=100)

    assert report.missing_in_marzban == []
    assert report.web_orders_without_access == []
    assert report.has_critical_findings() is False


async def test_audit_marzban_sync_treats_bound_web_order_as_normal(repo) -> None:
    username = "web_64c4dcd10b"
    await repo.upsert_user(592525300, username)
    await repo.create_web_order(
        order_id="64c4dcd10b2a416da357213871cf5872",
        provider="card",
        external_id="pay-bound",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="bound@example.com",
        pay_url="https://pay.example",
    )
    await repo.attach_web_order_access(
        order_id="64c4dcd10b2a416da357213871cf5872",
        marzban_username=username,
    )

    report = await audit_marzban_sync(
        repo,
        FakeMarzban([{"username": username, "status": "active", "expire": 0}]),
        limit=100,
    )

    assert report.non_standard_device_names == []
    assert report.shared_db_refs == []


async def test_audit_marzban_sync_ignores_disabled_orphan_marzban_user(repo) -> None:
    report = await audit_marzban_sync(
        repo,
        FakeMarzban([{"username": "web_disabled_orphan", "status": "disabled", "expire": 0}]),
        limit=100,
    )

    assert report.unknown_in_db == []
    assert report.has_findings() is False
