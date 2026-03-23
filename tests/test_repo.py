import time

import pytest_asyncio

import bot


@pytest_asyncio.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = bot.Repo(str(db_path))
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
        external_id="plan-4004",
        telegram_id=tg_id,
        days=30,
        gb=0,
        amount_rub=199.0,
        pay_url="https://pay.local/plan-4004",
        status="paid_applied",
        purpose="plan",
    )
    assert await repo.has_paid_plan_payment(tg_id) is True
