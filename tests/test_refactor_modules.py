from __future__ import annotations

from pathlib import Path

from config import normalize_public_base_url, parse_int_csv
from models import Plan
from utils import build_web_bind_payload, parse_web_order_from_payload


def test_parse_int_csv_in_config_module() -> None:
    assert parse_int_csv("72, 24, x, -1, 6, 24", default=(1,)) == (6, 24, 72)


def test_normalize_public_base_url_in_config_module() -> None:
    assert normalize_public_base_url(" https://rootvpn.tech/ ") == "https://rootvpn.tech"
    assert normalize_public_base_url("rootvpn.tech") == ""


def test_plan_model_smoke() -> None:
    plan = Plan(key="m1", title="1 месяц", days=30, gb=0, rub=99.0)
    assert plan.key == "m1"
    assert plan.days == 30


def test_web_bind_payload_roundtrip_in_utils_module() -> None:
    order_id = "bfc89cb5872a48ae91630429539f14b4"
    token = "test-token"
    payload = build_web_bind_payload(order_id, bot_token=token)
    assert payload.startswith("webbind_")
    assert parse_web_order_from_payload(payload, bot_token=token) == order_id


def test_bot_runtime_registers_extracted_user_runtime_handlers() -> None:
    text = Path("src/vpnbot/bot_runtime.py").read_text(encoding="utf-8")
    assert "register_user_runtime_handlers(" in text
    assert "UserRuntimeDeps(" in text


def test_bot_runtime_registers_fallback_after_specific_message_handlers() -> None:
    text = Path("src/vpnbot/bot_runtime.py").read_text(encoding="utf-8")
    fallback_pos = text.index("register_fallback_handler(")
    assert fallback_pos > text.index("register_user_message_handlers(")
    assert fallback_pos > text.index("register_user_runtime_handlers(")
    assert fallback_pos > text.index("register_admin_runtime_handlers(")
    assert fallback_pos > text.index("register_admin_message_handlers(")


def test_reply_keyboard_labels_have_matching_message_handlers() -> None:
    keyboard_text = Path("src/vpnbot/keyboards/bot_keyboards.py").read_text(encoding="utf-8")
    handler_text = (
        Path("src/vpnbot/handlers/bot_handlers_user_runtime.py").read_text(encoding="utf-8")
        + Path("src/vpnbot/handlers/bot_handlers_admin_runtime.py").read_text(encoding="utf-8")
    )
    labels = [
        "🔑 Получить подписку",
        "💳 Купить доступ",
        "📊 Мой статус",
        "📂 Еще",
        "🆘 Поддержка",
        "🛠 Админ-кабинет",
    ]
    for label in labels:
        assert label in keyboard_text
        assert label in handler_text

