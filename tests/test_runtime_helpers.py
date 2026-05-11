"""Tests for the pure helpers extracted from build_router into runtime_helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.runtime_helpers import (
    bind_web_order_to_user,
    list_replaceable_devices,
    replace_device_slot,
)


class FakeMarzban:
    """Minimal in-memory MarzbanClient stub with the same async surface we use."""

    def __init__(self, users: dict[str, dict[str, Any]] | None = None) -> None:
        self.users: dict[str, dict[str, Any]] = dict(users or {})
        self.created: list[str] = []
        self.modified: list[tuple[str, dict[str, Any]]] = []

    async def get_user(self, username: str) -> dict[str, Any] | None:
        user = self.users.get(username)
        return dict(user) if user else None

    async def create_user(
        self,
        *,
        username: str,
        expire: int = 0,
        data_limit: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "username": username,
            "expire": int(expire),
            "data_limit": int(data_limit),
            "status": "active",
        }
        self.users[username] = payload
        self.created.append(username)
        return dict(payload)

    async def modify_user(self, username: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.modified.append((username, dict(patch)))
        existing = self.users.setdefault(username, {"username": username})
        existing.update(patch)
        return dict(existing)


@dataclass
class EventCollector:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __call__(
        self,
        event_type: str,
        *,
        telegram_id: int | None = None,
        event_value: str = "",
        event_meta: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            (
                event_type,
                {
                    "telegram_id": telegram_id,
                    "event_value": event_value,
                    "event_meta": dict(event_meta or {}),
                },
            )
        )


class FakeSettings:
    def __init__(self, *, device_limit: int = 3) -> None:
        self.device_limit = device_limit


@pytest.fixture
async def repo(local_tmp_path):
    db_path = local_tmp_path / "bot.sqlite3"
    repo = Repo(str(db_path))
    await repo.open()
    try:
        yield repo
    finally:
        await repo.close()


# ---------- list_replaceable_devices ----------


async def test_list_replaceable_devices_returns_only_active(repo) -> None:
    tg = 1001
    await repo.upsert_user(tg, "tg_1001")
    await repo.upsert_device(tg, 2, "tg_1001_d2")
    await repo.upsert_device(tg, 3, "tg_1001_d3")
    marzban = FakeMarzban(
        users={
            "tg_1001": {"username": "tg_1001", "status": "active"},
            "tg_1001_d2": {"username": "tg_1001_d2", "status": "disabled"},
            # d3 deliberately missing from Marzban
        }
    )

    result = await list_replaceable_devices(tg, repo=repo, marzban=marzban)

    usernames = {row["marzban_username"] for row in result}
    assert usernames == {"tg_1001"}, "only active users present in Marzban should be returned"


async def test_list_replaceable_devices_skips_empty_username(repo) -> None:
    tg = 2002
    await repo.upsert_device(tg, 2, "")
    marzban = FakeMarzban()
    assert await list_replaceable_devices(tg, repo=repo, marzban=marzban) == []


# ---------- replace_device_slot ----------


async def test_replace_device_slot_creates_new_user_and_disables_old(repo) -> None:
    tg = 3003
    await repo.upsert_user(tg, "tg_3003")
    marzban = FakeMarzban(
        users={
            "tg_3003": {
                "username": "tg_3003",
                "expire": 1234567890,
                "data_limit": 5_000_000_000,
                "status": "active",
            }
        }
    )

    old, new, payload = await replace_device_slot(
        telegram_id=tg, slot=1, repo=repo, marzban=marzban
    )

    assert old == "tg_3003"
    assert new.startswith("tg_3003_r"), "replacement username must use _r prefix"
    assert payload["username"] == new
    assert payload["expire"] == 1234567890
    assert ("tg_3003", {"status": "disabled"}) in marzban.modified
    # slot 1 also updates the users table
    user_row = await repo.get_user(tg)
    assert user_row is not None
    assert user_row["marzban_username"] == new


async def test_replace_device_slot_raises_when_old_marzban_missing(repo) -> None:
    tg = 4004
    await repo.upsert_user(tg, "tg_4004")
    marzban = FakeMarzban()  # empty: old user not in Marzban
    with pytest.raises(RuntimeError, match="не найден в Marzban"):
        await replace_device_slot(telegram_id=tg, slot=1, repo=repo, marzban=marzban)


# ---------- bind_web_order_to_user ----------


def _make_order_kwargs(**overrides: Any) -> dict[str, Any]:
    base = dict(
        provider="card",
        external_id="pay-1",
        status="paid_applied",
        plan_key="m1",
        days=30,
        gb=0,
        amount_rub=99,
        customer_contact="client@example.com",
        pay_url="https://pay.test/ord",
    )
    base.update(overrides)
    return base


async def _seed_paid_order(repo, *, order_id: str, marzban_username: str = "web_abcd1234", **overrides) -> None:
    await repo.create_web_order(order_id=order_id, **_make_order_kwargs(**overrides))
    # paid orders carry the username assigned during fulfillment.
    if overrides.get("status", "paid_applied") == "paid_applied":
        await repo.attach_web_order_access(order_id=order_id, marzban_username=marzban_username)


async def test_bind_web_order_not_found(repo) -> None:
    marzban = FakeMarzban()
    settings = FakeSettings()
    track = EventCollector()
    ok, msg = await bind_web_order_to_user(
        telegram_id=1,
        order_id="missing",
        repo=repo,
        marzban=marzban,
        settings=settings,
        track_event=track,
    )
    assert ok is False
    assert "Заказ не найден" in msg


async def test_bind_web_order_not_paid_yet(repo) -> None:
    await _seed_paid_order(repo, order_id="ord-1", status="created")
    marzban = FakeMarzban()
    ok, msg = await bind_web_order_to_user(
        telegram_id=1,
        order_id="ord-1",
        repo=repo,
        marzban=marzban,
        settings=FakeSettings(),
        track_event=EventCollector(),
    )
    assert ok is False
    assert "не подтверждена" in msg


async def test_bind_web_order_new_telegram_user_gets_slot_1(repo) -> None:
    await _seed_paid_order(repo, order_id="ord-2")
    marzban = FakeMarzban(
        users={
            "web_abcd1234": {
                "username": "web_abcd1234",
                "expire": 2_000_000_000,
                "data_limit": 0,
                "status": "active",
            }
        }
    )
    track = EventCollector()
    tg = 5005

    ok, msg = await bind_web_order_to_user(
        telegram_id=tg,
        order_id="ord-2",
        repo=repo,
        marzban=marzban,
        settings=FakeSettings(),
        track_event=track,
    )

    assert ok is True
    assert "привязан" in msg
    # New Marzban user tg_5005 created, web_abcd1234 disabled
    assert "tg_5005" in marzban.users
    assert ("web_abcd1234", {"status": "disabled"}) in marzban.modified
    # repo.upsert_user called for slot 1
    user_row = await repo.get_user(tg)
    assert user_row is not None and user_row["marzban_username"] == "tg_5005"
    # tracking event emitted
    assert track.events and track.events[0][0] == "web_order_bound"
    assert track.events[0][1]["event_value"] == "slot_1"


async def test_bind_web_order_rejects_when_owned_by_other_tg(repo) -> None:
    await _seed_paid_order(repo, order_id="ord-3")
    # Another telegram user already owns the source username.
    other_tg = 7777
    await repo.upsert_user(other_tg, "web_abcd1234")
    marzban = FakeMarzban(
        users={
            "web_abcd1234": {"username": "web_abcd1234", "status": "active"},
        }
    )

    ok, msg = await bind_web_order_to_user(
        telegram_id=6006,
        order_id="ord-3",
        repo=repo,
        marzban=marzban,
        settings=FakeSettings(),
        track_event=EventCollector(),
    )

    assert ok is False
    assert "к другому Telegram" in msg


async def test_bind_web_order_existing_tg_gets_next_slot(repo) -> None:
    tg = 8008
    # Pre-existing primary slot
    await repo.upsert_user(tg, "tg_8008")
    await _seed_paid_order(repo, order_id="ord-4")
    marzban = FakeMarzban(
        users={
            "tg_8008": {"username": "tg_8008", "status": "active"},
            "web_abcd1234": {
                "username": "web_abcd1234",
                "expire": 0,
                "data_limit": 0,
                "status": "active",
            },
        }
    )
    track = EventCollector()

    ok, msg = await bind_web_order_to_user(
        telegram_id=tg,
        order_id="ord-4",
        repo=repo,
        marzban=marzban,
        settings=FakeSettings(device_limit=3),
        track_event=track,
    )

    assert ok is True
    assert "устройство #2" in msg, "should land on slot 2 since slot 1 already taken"
    assert "tg_8008_d2" in marzban.users
    assert track.events[0][1]["event_value"] == "slot_2"


async def test_bind_web_order_slot_limit_reached(repo) -> None:
    tg = 9009
    await repo.upsert_user(tg, "tg_9009")
    await repo.upsert_device(tg, 2, "tg_9009_d2")
    await _seed_paid_order(repo, order_id="ord-5")
    marzban = FakeMarzban(
        users={
            "tg_9009": {"username": "tg_9009", "status": "active"},
            "tg_9009_d2": {"username": "tg_9009_d2", "status": "active"},
            "web_abcd1234": {"username": "web_abcd1234", "status": "active"},
        }
    )

    ok, msg = await bind_web_order_to_user(
        telegram_id=tg,
        order_id="ord-5",
        repo=repo,
        marzban=marzban,
        settings=FakeSettings(device_limit=2),
        track_event=EventCollector(),
    )

    assert ok is False
    assert "лимит" in msg.lower()
