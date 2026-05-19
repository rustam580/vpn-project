from __future__ import annotations

from src.vpnbot.keyboards.bot_keyboards import admin_panel_keyboard
from src.vpnbot.handlers.bot_handlers_admin_runtime import rescue_status_keyboard


def test_admin_panel_has_sync_audit_button() -> None:
    keyboard = admin_panel_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "admin:sync_audit" for button in buttons)


def test_admin_panel_has_payment_issues_button() -> None:
    keyboard = admin_panel_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "admin:payment_issues" for button in buttons)


def test_admin_panel_has_rescue_command_button() -> None:
    keyboard = admin_panel_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.switch_inline_query_current_chat == "/rescue " for button in buttons)


def test_admin_panel_has_rescue_dashboard_button() -> None:
    keyboard = admin_panel_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "admin:rescue_dashboard" for button in buttons)


def test_admin_panel_has_rescue_pool_buttons() -> None:
    keyboard = admin_panel_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.switch_inline_query_current_chat == "/rescue_rooms" for button in buttons)
    assert any(button.switch_inline_query_current_chat == "/rescue_create " for button in buttons)


def test_rescue_status_keyboard_callback_fits_telegram_limit() -> None:
    keyboard = rescue_status_keyboard("rs-20260518202449-386029735")
    button = keyboard.inline_keyboard[0][0]

    assert button.callback_data == "admin:rescue_status:rs-20260518202449-386029735"
    assert len(button.callback_data.encode("utf-8")) <= 64
