import re

import bot


def test_build_replacement_username_has_expected_shape() -> None:
    slot1 = bot.build_replacement_username(123, 1)
    slot2 = bot.build_replacement_username(123, 2)
    assert re.fullmatch(r"tg_123_r[0-9a-f]{8}", slot1)
    assert re.fullmatch(r"tg_123_d2_r[0-9a-f]{8}", slot2)


def test_normalize_device_name_trims_and_limits() -> None:
    assert bot.normalize_device_name("  iPhone   17  ") == "iPhone 17"
    assert bot.normalize_device_name("   ") is None
    assert bot.normalize_device_name("x" * 40, limit=8) == "x" * 8


def test_next_device_slot_for_limited_and_unlimited() -> None:
    assert bot.next_device_slot({1}, limit=3) == 2
    assert bot.next_device_slot({1, 2, 3}, limit=3) is None
    assert bot.next_device_slot({1, 2, 3}, limit=0) == 4


def test_coerce_env_value_parses_supported_types() -> None:
    assert bot.coerce_env_value("10", "int") == "10"
    assert bot.coerce_env_value("9,5", "float") == "9.50"
    assert bot.coerce_env_value("yes", "bool") == "1"
    assert bot.coerce_env_value("off", "bool") == "0"
    assert bot.coerce_env_value("x", "int") is None


def test_extract_links_deduplicates_and_ignores_invalid_values() -> None:
    user = {"links": [" vless://a ", "vless://a", "", 123, "vless://b"]}
    assert bot.extract_links(user) == ["vless://a", "vless://b"]


def test_split_message_respects_limit() -> None:
    text = "\n".join(["line"] * 80)
    parts = bot.split_message(text, limit=60)
    assert len(parts) > 1
    assert all(len(part) <= 60 for part in parts)
