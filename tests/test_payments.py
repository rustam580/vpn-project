import time
from types import SimpleNamespace

import pytest_asyncio

import bot


class FakeMarzban:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}

    async def get_user(self, username: str):
        return self.users.get(username)

    async def create_user(self, *, username: str, expire: int, data_limit: int):
        user = {
            "username": username,
            "status": "active",
            "expire": expire,
            "data_limit": data_limit,
            "used_traffic": 0,
            "links": [f"vless://{username}"],
        }
        self.users[username] = user
        return user

    async def modify_user(self, username: str, payload: dict):
        user = self.users.get(username)
        if user is None:
            user = await self.create_user(username=username, expire=0, data_limit=0)
        user.update(payload)
        return user


@pytest_asyncio.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = bot.Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


async def test_check_payment_requeues_stale_processing_and_applies(
    repo, monkeypatch
) -> None:
    tg_id = 5005
    external_id = "inv-stale-1"
    slot = 2
    username = f"tg_{tg_id}_d{slot}"

    marzban = FakeMarzban()
    await marzban.create_user(username=f"tg_{tg_id}", expire=1700000000, data_limit=0)
    await marzban.create_user(username=username, expire=1700000000, data_limit=0)

    await repo.upsert_user(tg_id, f"tg_{tg_id}")
    await repo.upsert_device(tg_id, slot, username)
    await repo.upsert_payment(
        provider="crypto",
        external_id=external_id,
        telegram_id=tg_id,
        days=0,
        gb=0,
        amount_rub=99,
        pay_url="https://pay.local/inv-stale-1",
        status="processing",
        purpose="device_add",
        device_slot=slot,
    )

    assert repo.conn is not None
    await repo.conn.execute(
        "UPDATE payments SET updated_at = ? WHERE provider = ? AND external_id = ?",
        (int(time.time()) - 5000, "crypto", external_id),
    )
    await repo.conn.commit()

    async def fake_crypto_status(_settings, _external_id):
        return "paid"

    monkeypatch.setattr(bot, "cryptobot_check_invoice", fake_crypto_status)

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
        marzban=marzban,
        settings=settings,
        bot=None,
    )
    assert result.startswith("✅ Устройство 2 добавлено")
    assert updated is not None
    payment = await repo.get_payment("crypto", external_id)
    assert payment is not None
    assert payment["status"] == "paid_applied"


async def test_check_payment_rejects_foreign_user(repo) -> None:
    await repo.upsert_payment(
        provider="crypto",
        external_id="inv-foreign-1",
        telegram_id=6006,
        days=30,
        gb=0,
        amount_rub=99,
        pay_url="https://pay.local/inv-foreign-1",
        status="pending",
        purpose="plan",
    )

    result, updated = await bot.check_and_apply_payment(
        provider="crypto",
        external_id="inv-foreign-1",
        telegram_id=7007,
        repo=repo,
        marzban=FakeMarzban(),
        settings=SimpleNamespace(payment_processing_requeue_seconds=120, device_limit=1, trial_days=1, trial_gb=0),
        bot=None,
    )
    assert "другого пользователя" in result
    assert updated is None
