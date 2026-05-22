from __future__ import annotations

from datetime import datetime, timezone

from src.vpnbot.messaging import _device_label_with_expire


def test_device_label_with_expire_shows_slot_term() -> None:
    expire_ts = int(datetime(2026, 8, 17, 15, 15, tzinfo=timezone.utc).timestamp())

    label = _device_label_with_expire("Устройство 1", {"expire": expire_ts})

    assert label.startswith("Устройство 1 — доступ до 17.08.2026 15:15 UTC")


def test_device_label_with_expire_handles_unlimited() -> None:
    label = _device_label_with_expire("Устройство 2", {"expire": 0})

    assert label == "Устройство 2 — доступ без срока"
