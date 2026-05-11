"""Tests for drift-resolution keyboard + callback parsing."""
from __future__ import annotations

from src.vpnbot.keyboards.drift_keyboards import (
    ACTION_DROP_DB_REF,
    ACTION_IGNORE,
    ACTION_RECREATE,
    ACTION_RETRY_WEB_ORDER,
    drift_finding_keyboard,
    encode_drift_callback,
    parse_drift_callback,
)
from src.vpnbot.marzban_sync import (
    KIND_MISSING_IN_MARZBAN,
    KIND_WEB_ORDER_NO_ACCESS,
    DriftFinding,
)


def test_parse_roundtrip_missing_tg():
    data = encode_drift_callback("m:tg_12345_d2", ACTION_RECREATE)
    parsed = parse_drift_callback(data)
    assert parsed == ("m:tg_12345_d2", ACTION_RECREATE)


def test_parse_roundtrip_web_order():
    data = encode_drift_callback("w:abcdef1234567890", ACTION_RETRY_WEB_ORDER)
    parsed = parse_drift_callback(data)
    assert parsed == ("w:abcdef1234567890", ACTION_RETRY_WEB_ORDER)


def test_parse_rejects_unknown_prefix():
    assert parse_drift_callback("xx:m:tg_1:r") is None


def test_parse_rejects_unknown_action():
    assert parse_drift_callback("da:m:tg_1:z") is None


def test_parse_rejects_empty_finding_id():
    assert parse_drift_callback("da::r") is None


def test_parse_rejects_too_short():
    assert parse_drift_callback("da:tg_1") is None
    assert parse_drift_callback("") is None


def test_keyboard_for_tg_missing_offers_recreate_and_drop_and_ignore():
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:tg_1",
        summary="x",
        payload={"username": "tg_1"},
    )
    kb = drift_finding_keyboard(finding)
    actions = {
        parse_drift_callback(btn.callback_data)[1]
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and parse_drift_callback(btn.callback_data)
    }
    assert actions == {ACTION_RECREATE, ACTION_DROP_DB_REF, ACTION_IGNORE}


def test_keyboard_for_web_missing_omits_recreate():
    finding = DriftFinding(
        kind=KIND_MISSING_IN_MARZBAN,
        finding_id="m:web_x",
        summary="x",
        payload={"username": "web_xxxx"},
    )
    kb = drift_finding_keyboard(finding)
    actions = {
        parse_drift_callback(btn.callback_data)[1]
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and parse_drift_callback(btn.callback_data)
    }
    assert ACTION_RECREATE not in actions
    assert ACTION_DROP_DB_REF in actions
    assert ACTION_IGNORE in actions


def test_keyboard_for_web_order_no_access_offers_retry():
    finding = DriftFinding(
        kind=KIND_WEB_ORDER_NO_ACCESS,
        finding_id="w:ord-1",
        summary="x",
        payload={"order_id": "ord-1"},
    )
    kb = drift_finding_keyboard(finding)
    actions = {
        parse_drift_callback(btn.callback_data)[1]
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and parse_drift_callback(btn.callback_data)
    }
    assert actions == {ACTION_RETRY_WEB_ORDER, ACTION_IGNORE}


def test_callback_data_fits_64_bytes():
    # Realistic worst-case: web order with a 32-hex UUID.
    long_finding = DriftFinding(
        kind=KIND_WEB_ORDER_NO_ACCESS,
        finding_id="w:" + "a" * 32,
        summary="x",
        payload={"order_id": "a" * 32},
    )
    kb = drift_finding_keyboard(long_finding)
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                assert len(btn.callback_data.encode("utf-8")) <= 64, btn.callback_data
