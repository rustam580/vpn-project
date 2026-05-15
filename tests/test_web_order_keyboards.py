from __future__ import annotations

from src.vpnbot.keyboards.web_order_keyboards import (
    ACTION_CHECK_PAYMENT,
    encode_web_order_callback,
    parse_web_order_callback,
    web_order_support_keyboard,
)


def _buttons(kb):
    return [button for row in kb.inline_keyboard for button in row]


def test_web_order_callback_roundtrip() -> None:
    data = encode_web_order_callback(ACTION_CHECK_PAYMENT, "abc123")
    assert parse_web_order_callback(data) == (ACTION_CHECK_PAYMENT, "abc123")


def test_web_order_callback_rejects_bad_data() -> None:
    assert parse_web_order_callback("") is None
    assert parse_web_order_callback("admin:sync_audit") is None
    assert parse_web_order_callback("wo:x:abc123") is None
    assert parse_web_order_callback("wo:c:") is None
    assert parse_web_order_callback("wo:c") is None


def test_web_order_keyboard_has_safe_support_actions() -> None:
    kb = web_order_support_keyboard(
        {
            "order_id": "order-1",
            "provider": "card",
            "external_id": "payment-1",
        },
        linked_tg_ids={12345},
    )

    assert kb is not None
    buttons = _buttons(kb)
    callback_data = {button.callback_data for button in buttons if button.callback_data}
    switch_queries = {
        button.switch_inline_query_current_chat
        for button in buttons
        if button.switch_inline_query_current_chat
    }

    assert encode_web_order_callback(ACTION_CHECK_PAYMENT, "order-1") in callback_data
    assert "admin:sync_audit" in callback_data
    assert "/user 12345" in switch_queries


def test_web_order_keyboard_callback_data_fits_telegram_limit() -> None:
    kb = web_order_support_keyboard(
        {
            "order_id": "a" * 32,
            "provider": "card",
            "external_id": "3171794b-000f-5001-8000-1b5a1bd32f49",
        },
        linked_tg_ids={12345, 67890},
    )

    assert kb is not None
    for button in _buttons(kb):
        if button.callback_data:
            assert len(button.callback_data.encode("utf-8")) <= 64


def test_web_order_keyboard_omits_payment_check_when_payment_ids_missing() -> None:
    kb = web_order_support_keyboard({"order_id": "order-1"}, linked_tg_ids=set())

    assert kb is not None
    callback_data = {button.callback_data for button in _buttons(kb) if button.callback_data}
    assert encode_web_order_callback(ACTION_CHECK_PAYMENT, "order-1") not in callback_data
    assert "admin:sync_audit" in callback_data
