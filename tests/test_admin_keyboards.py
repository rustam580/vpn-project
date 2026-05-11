from __future__ import annotations

from src.vpnbot.keyboards.bot_keyboards import admin_panel_keyboard


def test_admin_panel_has_sync_audit_button() -> None:
    keyboard = admin_panel_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "admin:sync_audit" for button in buttons)
