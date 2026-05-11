"""Tests for safe drift-resolver actions in src/vpnbot/drift_resolver.py."""
from __future__ import annotations

import json
from typing import Any

import pytest

from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.drift_resolver import (
    EVENT_DRIFT_IGNORED,
    EVENT_DRIFT_RESOLVED,
    drop_missing_marzban_db_ref,
    ignore_drift,
    recreate_missing_marzban_user,
    retry_web_order_access,
)
from src.vpnbot.marzban_sync import (
    KIND_MISSING_IN_MARZBAN,
    KIND_NON_STANDARD_DEVICE,
    KIND_WEB_ORDER_NO_ACCESS,
    DriftFinding,
    audit_marzban_sync,
)


class FakeMarzban:
    def __init__(self, users: dict[str, dict[str, Any]] | None = None) -> None:
        self.users: dict[str, dict[str, Any]] = dict(users or {})
        self.created: list[dict[str, Any]] = []

    async def get_user(self, username: str):
        return dict(self.users[username]) if username in self.users else None

    async def create_user(self, *, username: str, expire: int = 0, data_limit: int = 0):
        payload = {
            "username": username,
            "expire": int(expire),
            "data_limit": int(data_limit),
            "status": "active",
        }
        self.users[username] = payload
        self.created.append(dict(payload))
        return dict(payload)


class FakeSettings:
    def __init__(self, *, trial_days: int = 1, trial_gb: int = 5) -> None:
        self.trial_days = trial_days
        self.trial_gb = trial_gb


@pytest.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


async def _events(repo: Repo, event_type: str) -> list[dict[str, Any]]:
    assert repo.conn is not None
    cursor = await repo.conn.execute(
        "SELECT telegram_id, event_value, event_meta FROM events WHERE event_type = ? ORDER BY id",
        (event_type,),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [
        {
            "telegram_id": row["telegram_id"],
            "event_value": row["event_value"],
            "event_meta": json.loads(row["event_meta"]) if row["event_meta"] else {},
        }
        for row in rows
    ]


# ---------- recreate_missing_marzban_user ----------


async def test_recreate_creates_user_with_trial_params(repo) -> None:
    marzban = FakeMarzban()
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_1001",
        summary="tg_1001 <- tg=1001",
        payload={"username": "tg_1001", "refs": []},
    )
    result = await recreate_missing_marzban_user(
        finding,
        repo=repo,
        marzban=marzban,
        settings=FakeSettings(trial_days=3, trial_gb=10),
        actor_tg=42,
    )
    assert result.ok is True
    assert "tg_1001" in marzban.users
    assert marzban.created[0]["data_limit"] == 10 * 1_000_000_000
    events = await _events(repo, EVENT_DRIFT_RESOLVED)
    assert events and events[0]["event_value"] == "m:tg_1001"
    assert events[0]["event_meta"]["action"] == "recreate"
    assert events[0]["event_meta"]["days"] == 3


async def test_recreate_short_circuits_when_user_already_present(repo) -> None:
    marzban = FakeMarzban(users={"tg_1002": {"username": "tg_1002", "status": "active"}})
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_1002",
        summary="tg_1002 <- tg=1002",
        payload={"username": "tg_1002", "refs": []},
    )
    result = await recreate_missing_marzban_user(
        finding, repo=repo, marzban=marzban, settings=FakeSettings()
    )
    assert result.ok is True
    assert "уже существует" in result.message
    assert marzban.created == [], "must not create a duplicate"
    events = await _events(repo, EVENT_DRIFT_RESOLVED)
    assert events[0]["event_meta"]["result"] == "already_exists"


async def test_recreate_refuses_web_username(repo) -> None:
    marzban = FakeMarzban()
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:web_deadbeef",
        summary="web_deadbeef <- order=ord-1",
        payload={"username": "web_deadbeef", "refs": []},
    )
    result = await recreate_missing_marzban_user(
        finding, repo=repo, marzban=marzban, settings=FakeSettings()
    )
    assert result.ok is False
    assert "tg_*" in result.message
    assert marzban.created == []


async def test_recreate_rejects_wrong_kind(repo) -> None:
    finding = DriftFinding(
        kind=KIND_NON_STANDARD_DEVICE,
        finding_id="n:1:2",
        summary="x",
        payload={"username": "tg_1"},
    )
    result = await recreate_missing_marzban_user(
        finding, repo=repo, marzban=FakeMarzban(), settings=FakeSettings()
    )
    assert result.ok is False


# ---------- drop_missing_marzban_db_ref ----------


async def test_drop_db_ref_removes_users_and_devices_rows(repo) -> None:
    tg = 2002
    await repo.upsert_user(tg, "tg_2002")
    await repo.upsert_device(tg, 2, "tg_2002_d2")
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_2002",
        summary="tg_2002 <- ...",
        payload={
            "username": "tg_2002",
            "refs": [
                {"source": "users", "telegram_id": tg, "device_id": 1, "username": "tg_2002", "detail": ""},
            ],
        },
    )
    result = await drop_missing_marzban_db_ref(finding, repo=repo, actor_tg=99)
    assert result.ok is True
    # users.marzban_username should now be cleared (NOT NULL schema -> empty string).
    user_row = await repo.get_user(tg)
    assert user_row is not None
    assert user_row["marzban_username"] in ("", None)
    events = await _events(repo, EVENT_DRIFT_RESOLVED)
    assert events and events[0]["event_meta"]["action"] == "drop_db_ref"


async def test_drop_db_ref_skips_web_orders_audit_trail(repo) -> None:
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:web_x",
        summary="x",
        payload={
            "username": "web_x",
            "refs": [
                {"source": "web_orders", "telegram_id": None, "device_id": None, "username": "web_x", "detail": ""},
            ],
        },
    )
    result = await drop_missing_marzban_db_ref(finding, repo=repo)
    assert result.ok is False, "drop should refuse when only web_orders refs are present"
    assert "Нет безопасных" in result.message


async def test_drop_db_ref_removes_device_row(repo) -> None:
    tg = 3003
    await repo.upsert_device(tg, 3, "tg_3003_d3", "phone")
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_3003_d3",
        summary="",
        payload={
            "username": "tg_3003_d3",
            "refs": [
                {
                    "source": "devices",
                    "telegram_id": tg,
                    "device_id": 3,
                    "username": "tg_3003_d3",
                    "detail": "tg=3003 slot=3",
                }
            ],
        },
    )
    result = await drop_missing_marzban_db_ref(finding, repo=repo, actor_tg=5)
    assert result.ok is True
    assert await repo.get_device(tg, 3) is None


# ---------- retry_web_order_access ----------


async def test_retry_web_order_creates_marzban_user_and_attaches(repo) -> None:
    await repo.create_web_order(
        order_id="ord-retry",
        provider="card",
        external_id="pay-r",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=50,
        amount_rub=99,
        customer_contact="x@example.com",
        pay_url="https://pay.example",
    )
    marzban = FakeMarzban()
    finding = DriftFinding(
        kind=KIND_WEB_ORDER_NO_ACCESS,
        finding_id="w:ord-retry",
        summary="order=ord-retry plan=m1",
        payload={"order_id": "ord-retry", "plan_key": "m1", "days": 30, "gb": 50},
    )

    result = await retry_web_order_access(
        finding, repo=repo, marzban=marzban, settings=FakeSettings(), actor_tg=7
    )

    assert result.ok is True
    assert len(marzban.created) == 1
    new_username = marzban.created[0]["username"]
    assert new_username == "web_ord-retry"
    assert marzban.created[0]["data_limit"] == 50 * 1_000_000_000
    order_row = await repo.get_web_order("ord-retry")
    assert order_row["marzban_username"] == new_username


async def test_retry_web_order_skips_when_username_already_set(repo) -> None:
    await repo.create_web_order(
        order_id="ord-set",
        provider="card",
        external_id="pay-s",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="x@example.com",
        pay_url="https://pay.example",
    )
    await repo.attach_web_order_access(order_id="ord-set", marzban_username="web_existing")

    marzban = FakeMarzban()
    finding = DriftFinding(
        kind=KIND_WEB_ORDER_NO_ACCESS,
        finding_id="w:ord-set",
        summary="x",
        payload={"order_id": "ord-set", "plan_key": "m1", "days": 30, "gb": 0},
    )
    result = await retry_web_order_access(
        finding, repo=repo, marzban=marzban, settings=FakeSettings()
    )
    assert result.ok is True
    assert "уже привязан" in result.message
    assert marzban.created == []


async def test_retry_web_order_refuses_non_paid_status(repo) -> None:
    await repo.create_web_order(
        order_id="ord-nx",
        provider="card",
        external_id="pay-n",
        status="created",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="x@example.com",
        pay_url="https://pay.example",
    )
    finding = DriftFinding(
        kind=KIND_WEB_ORDER_NO_ACCESS,
        finding_id="w:ord-nx",
        summary="x",
        payload={"order_id": "ord-nx"},
    )
    result = await retry_web_order_access(
        finding, repo=repo, marzban=FakeMarzban(), settings=FakeSettings()
    )
    assert result.ok is False
    assert "статус заказа" in result.message.lower()


# ---------- ignore_drift ----------


async def test_ignore_writes_event_with_finding_id(repo) -> None:
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_777",
        summary="tg_777 <- tg=777",
        payload={"username": "tg_777"},
    )
    result = await ignore_drift(finding, repo=repo, actor_tg=42, note="test stale")
    assert result.ok is True
    events = await _events(repo, EVENT_DRIFT_IGNORED)
    assert events and events[0]["event_value"] == "m:tg_777"
    assert events[0]["event_meta"]["note"] == "test stale"


async def test_ignored_finding_is_hidden_from_later_audit(repo) -> None:
    await repo.upsert_user(888, "tg_888")
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_888",
        summary="tg_888 <- tg=888",
        payload={"username": "tg_888"},
    )
    await ignore_drift(finding, repo=repo, actor_tg=42)

    marzban = FakeMarzban()

    async def req(method, path, **kwargs):
        return {"users": [], "total": 0}

    marzban.req = req  # type: ignore[attr-defined]
    report = await audit_marzban_sync(repo, marzban, limit=10)

    assert report.has_critical_findings() is False
    assert report.find_by_id("m:tg_888") is None
    assert "m:tg_888" in report.ignored_finding_ids


# ---------- integration: audit produces findings consumable by resolver ----------


async def test_audit_yields_findings_with_stable_ids(repo) -> None:
    """End-to-end: audit -> findings -> resolver consumes them."""
    await repo.upsert_user(5005, "tg_5005")
    await repo.create_web_order(
        order_id="ord-int",
        provider="card",
        external_id="pay-int",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="x@example.com",
        pay_url="https://pay.example",
    )
    marzban = FakeMarzban()  # tg_5005 is missing, ord-int has no username
    # Stub `req` because audit_marzban_sync calls list_marzban_users first.

    async def req(method, path, **kwargs):
        return {"users": [], "total": 0}

    marzban.req = req  # type: ignore[attr-defined]

    report = await audit_marzban_sync(repo, marzban, limit=10)

    finding_ids = {f.finding_id for f in report.findings}
    assert "m:tg_5005" in finding_ids
    assert "w:ord-int" in finding_ids

    missing = report.find_by_id("m:tg_5005")
    assert missing is not None
    assert missing.payload["username"] == "tg_5005"

    web = report.find_by_id("w:ord-int")
    assert web is not None
    assert web.payload["order_id"] == "ord-int"
    assert web.payload["days"] == 30

    # Critical findings include both
    assert len(report.critical_findings()) == 2
