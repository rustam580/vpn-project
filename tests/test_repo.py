import sqlite3
import time

import pytest
import pytest_asyncio

from src.vpnbot.db import bot_repo
from src.vpnbot.db.bot_repo import Repo


@pytest_asyncio.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture
async def repo_conn(repo):
    assert repo.conn is not None
    return repo.conn


async def test_upsert_user_creates_primary_device(repo) -> None:
    await repo.upsert_user(1001, "tg_1001")
    user_row = await repo.get_user(1001)
    dev_row = await repo.get_device(1001, 1)
    assert user_row is not None
    assert dev_row is not None
    assert user_row["marzban_username"] == "tg_1001"
    assert dev_row["marzban_username"] == "tg_1001"


async def test_requeue_stuck_processing_payments(repo, repo_conn) -> None:
    await repo.upsert_payment(
        provider="crypto",
        external_id="inv-1",
        telegram_id=1001,
        days=30,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/inv-1",
        status="pending",
    )
    claimed = await repo.claim_payment_for_apply("crypto", "inv-1")
    assert claimed is True

    stale_ts = int(time.time()) - 3600
    await repo_conn.execute(
        "UPDATE payments SET updated_at = ? WHERE provider = ? AND external_id = ?",
        (stale_ts, "crypto", "inv-1"),
    )
    await repo_conn.commit()

    rows = await repo.requeue_stuck_processing_payments(
        "crypto",
        older_than_sec=300,
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0]["external_id"] == "inv-1"

    payment = await repo.get_payment("crypto", "inv-1")
    assert payment is not None
    assert payment["status"] == "pending"


async def test_list_device_usernames_returns_unique_values(repo) -> None:
    await repo.upsert_device(2002, 1, "tg_2002")
    await repo.upsert_device(2002, 2, "tg_2002_d2")
    names = await repo.list_device_usernames(2002)
    assert names == ["tg_2002", "tg_2002_d2"]


async def test_list_known_telegram_ids_unions_all_sources(repo) -> None:
    await repo.upsert_user(3001, "tg_3001")
    await repo.upsert_device(3002, 2, "tg_3002_d2")
    await repo.upsert_payment(
        provider="crypto",
        external_id="inv-known-1",
        telegram_id=3003,
        days=30,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/inv-known-1",
        status="pending",
    )
    await repo.touch_chat(3004)
    ids = await repo.list_known_telegram_ids()
    assert ids == [3001, 3002, 3003, 3004]


async def test_find_web_orders_matches_order_contact_and_username(repo) -> None:
    await repo.create_web_order(
        order_id="abc123order",
        provider="card",
        external_id="pay-abc-1",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99.0,
        customer_contact="client@example.com",
        pay_url="https://pay.local/abc",
    )
    await repo.attach_web_order_access(order_id="abc123order", marzban_username="web_abc123")

    by_order = await repo.find_web_orders("abc123order")
    by_contact = await repo.find_web_orders("client@example.com")
    by_username = await repo.find_web_orders("web_abc123")

    assert [row["order_id"] for row in by_order] == ["abc123order"]
    assert [row["order_id"] for row in by_contact] == ["abc123order"]
    assert [row["order_id"] for row in by_username] == ["abc123order"]


async def test_has_paid_plan_payment_counts_only_plan_paid(repo) -> None:
    tg_id = 4004
    assert await repo.has_paid_plan_payment(tg_id) is False

    await repo.upsert_payment(
        provider="card",
        external_id="dev-4004",
        telegram_id=tg_id,
        days=0,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/dev-4004",
        status="paid_applied",
        purpose="device_add",
        device_slot=2,
    )
    assert await repo.has_paid_plan_payment(tg_id) is False

    await repo.upsert_payment(
        provider="card",
        external_id="plan-device-4004",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=199.0,
        pay_url="https://pay.local/plan-device-4004",
        status="paid_applied",
        purpose="plan_device",
        device_slot=1,
    )
    assert await repo.has_paid_plan_payment(tg_id) is True

    await repo.upsert_payment(
        provider="card",
        external_id="plan-all-4004",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=199.0,
        pay_url="https://pay.local/plan-all-4004",
        status="paid_applied",
        purpose="plan_all",
    )
    assert await repo.has_paid_plan_payment(tg_id) is True


async def test_events_summary_and_latest_payment(repo, repo_conn) -> None:
    tg_id = 5006
    now = int(time.time())

    await repo.log_event(event_type="user_start", telegram_id=tg_id)
    await repo.log_event(event_type="config_requested", telegram_id=tg_id)
    await repo.log_event(event_type="payment_created_plan", telegram_id=tg_id)
    summary = await repo.event_counts_since(now - 60)
    assert summary["user_start"]["total"] >= 1
    assert summary["config_requested"]["users"] == 1

    await repo.upsert_payment(
        provider="card",
        external_id="older-pay",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=99.0,
        pay_url="https://pay.local/older-pay",
        status="pending",
        purpose="plan",
    )
    await repo.upsert_payment(
        provider="card",
        external_id="new-pay",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=199.0,
        pay_url="https://pay.local/new-pay",
        status="paid_applied",
        purpose="plan",
    )
    await repo_conn.execute(
        "UPDATE payments SET updated_at = ? WHERE external_id = ?",
        (now - 10, "older-pay"),
    )
    await repo_conn.execute(
        "UPDATE payments SET updated_at = ? WHERE external_id = ?",
        (now + 10, "new-pay"),
    )
    await repo_conn.commit()

    latest = await repo.get_latest_payment(tg_id)
    assert latest is not None
    assert latest["external_id"] == "new-pay"

async def test_notification_mark_is_deduplicated(repo) -> None:
    first = await repo.mark_notification_once(
        telegram_id=9001,
        device_id=2,
        mark_type="renewal_reminder_24h",
        expire_ts=1700000000,
    )
    second = await repo.mark_notification_once(
        telegram_id=9001,
        device_id=2,
        mark_type="renewal_reminder_24h",
        expire_ts=1700000000,
    )
    assert first is True
    assert second is False


async def test_delete_notification_mark_allows_retry(repo) -> None:
    created = await repo.mark_notification_once(
        telegram_id=9002,
        device_id=0,
        mark_type="sub_migration_prompt",
        expire_ts=1700000000,
    )
    assert created is True
    removed = await repo.delete_notification_mark(
        telegram_id=9002,
        device_id=0,
        mark_type="sub_migration_prompt",
        expire_ts=1700000000,
    )
    assert removed == 1
    created_again = await repo.mark_notification_once(
        telegram_id=9002,
        device_id=0,
        mark_type="sub_migration_prompt",
        expire_ts=1700000000,
    )
    assert created_again is True


async def test_prune_subscription_hits_removes_old_rows(repo, repo_conn) -> None:
    now = int(time.time())
    await repo_conn.execute(
        """
        INSERT INTO subscription_hits (
            telegram_id, marzban_username, token, client_ip, user_agent,
            raw_count, unique_count, was_deduped, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (9011, "tg_9011", "hash-old", "1.1.1.1", "ua-old", 2, 1, 1, now - 10 * 86400),
    )
    await repo_conn.execute(
        """
        INSERT INTO subscription_hits (
            telegram_id, marzban_username, token, client_ip, user_agent,
            raw_count, unique_count, was_deduped, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (9011, "tg_9011", "hash-new", "1.1.1.1", "ua-new", 1, 1, 0, now),
    )
    await repo_conn.commit()

    removed = await repo.prune_subscription_hits(older_than_sec=3 * 86400)
    assert removed == 1

    cur = await repo_conn.execute("SELECT token FROM subscription_hits ORDER BY created_at ASC")
    rows = await cur.fetchall()
    await cur.close()
    assert [str(row["token"]) for row in rows] == ["hash-new"]


async def test_has_open_plan_payment_detects_pending(repo) -> None:
    tg_id = 9010
    assert (
        await repo.has_open_plan_payment(
            telegram_id=tg_id,
            purpose="plan_all",
            device_slot=0,
        )
    ) is False

    await repo.upsert_payment(
        provider="card",
        external_id="open-plan-9010",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=199.0,
        pay_url="https://pay.local/open-plan-9010",
        status="pending",
        purpose="plan_all",
        device_slot=0,
    )

    assert (
        await repo.has_open_plan_payment(
            telegram_id=tg_id,
            purpose="plan_all",
            device_slot=0,
        )
    ) is True


async def test_schema_version_is_latest_after_open(repo, repo_conn) -> None:
    cur = await repo_conn.execute("SELECT version FROM schema_version LIMIT 1")
    row = await cur.fetchone()
    await cur.close()
    assert row is not None
    assert int(row["version"]) == bot_repo.SCHEMA_VERSION_LATEST


async def test_repo_migrates_legacy_db(local_tmp_path) -> None:
    db_path = local_tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                telegram_id INTEGER PRIMARY KEY,
                marzban_username TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE payments (
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                days INTEGER NOT NULL,
                gb INTEGER NOT NULL,
                amount_rub REAL NOT NULL,
                pay_url TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(provider, external_id)
            );
            CREATE TABLE devices (
                telegram_id INTEGER NOT NULL,
                device_id INTEGER NOT NULL,
                marzban_username TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id, device_id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    migrated_repo = Repo(str(db_path))
    await migrated_repo.open()
    try:
        assert migrated_repo.conn is not None
        cur = await migrated_repo.conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        assert row is not None
        assert int(row["version"]) == bot_repo.SCHEMA_VERSION_LATEST

        cur = await migrated_repo.conn.execute("PRAGMA table_info(payments)")
        payment_cols = await cur.fetchall()
        await cur.close()
        payment_col_names = {str(col["name"]) for col in payment_cols}
        assert {"purpose", "device_slot"}.issubset(payment_col_names)

        cur = await migrated_repo.conn.execute("PRAGMA table_info(devices)")
        device_cols = await cur.fetchall()
        await cur.close()
        device_col_names = {str(col["name"]) for col in device_cols}
        assert "device_name" in device_col_names
    finally:
        await migrated_repo.close()


async def test_web_order_lifecycle(repo) -> None:
    await repo.create_web_order(
        order_id="ord-1",
        provider="card",
        external_id="pay-1",
        status="pending",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99.0,
        customer_contact="@user",
        pay_url="https://pay.local/pay-1",
    )

    row = await repo.get_web_order("ord-1")
    assert row is not None
    assert row["provider"] == "card"
    assert row["status"] == "pending"
    assert row["marzban_username"] is None

    await repo.set_web_order_status("ord-1", "succeeded")
    await repo.attach_web_order_access(order_id="ord-1", marzban_username="web_ord_1")

    row2 = await repo.get_web_order("ord-1")
    assert row2 is not None
    assert row2["status"] == "succeeded"
    assert row2["marzban_username"] == "web_ord_1"


async def test_web_order_upsert_by_order_id(repo) -> None:
    await repo.create_web_order(
        order_id="ord-2",
        provider="card",
        external_id="pay-2",
        status="pending",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99.0,
        customer_contact="first-contact",
        pay_url="https://pay.local/pay-2",
    )
    await repo.create_web_order(
        order_id="ord-2",
        provider="crypto",
        external_id="pay-2b",
        status="paid",
        plan_key="m3",
        days=90,
        gb=0,
        amount_rub=279.0,
        customer_contact="second-contact",
        pay_url="https://pay.local/pay-2b",
    )

    row = await repo.get_web_order("ord-2")
    assert row is not None
    assert row["provider"] == "crypto"
    assert row["external_id"] == "pay-2b"
    assert row["status"] == "paid"
    assert row["plan_key"] == "m3"
    assert row["days"] == 90


async def test_web_bind_conversion_stats_uses_event_order_ids_and_lookback(repo, repo_conn) -> None:
    now = int(time.time())

    await repo.log_event(
        event_type="web_order_paid_applied",
        event_meta={"order_id": "ord-a"},
    )
    await repo.log_event(
        event_type="web_order_paid_applied",
        event_meta={"order_id": "ord-b"},
    )
    await repo.log_event(
        event_type="web_order_paid_applied",
        event_meta={"order_id": "ord-c"},
    )
    await repo.log_event(
        event_type="web_order_bound",
        event_meta={"order_id": "ord-b"},
    )
    await repo.log_event(
        event_type="web_order_bound",
        event_meta={"order_id": "ord-c"},
    )
    await repo.log_event(
        event_type="web_order_bound",
        event_meta={"order_id": "ord-x"},
    )

    await repo.log_event(
        event_type="web_order_paid_applied",
        event_meta={"order_id": "ord-old"},
    )
    await repo.log_event(
        event_type="web_order_bound",
        event_meta={"order_id": "ord-old"},
    )
    await repo_conn.execute(
        """
        UPDATE events
        SET created_at = ?
        WHERE event_meta LIKE '%"order_id":"ord-old"%'
        """,
        (now - 10 * 86400,),
    )
    await repo_conn.commit()

    stats = await repo.web_bind_conversion_stats(days=7)
    assert stats["days"] == 7
    assert stats["paid_orders"] == 3
    assert stats["bound_orders"] == 3
    assert stats["bound_from_paid"] == 2
    assert stats["pending_bind"] == 1
    assert stats["conversion_pct"] == pytest.approx(66.666, rel=1e-2)
