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


def test_normalize_channel_url_accepts_common_formats() -> None:
    assert bot.normalize_channel_url("@rootvpn_news") == "https://t.me/rootvpn_news"
    assert bot.normalize_channel_url("t.me/rootvpn_news") == "https://t.me/rootvpn_news"
    assert bot.normalize_channel_url("https://t.me/rootvpn_news") == "https://t.me/rootvpn_news"
    assert bot.normalize_channel_url("rootvpn_news") == "https://t.me/rootvpn_news"
    assert bot.normalize_channel_url("  ") is None


def test_quick_connect_guide_contains_core_sections() -> None:
    text = bot.quick_connect_guide_text()
    assert "iOS" in text
    assert "Android" in text
    assert "Windows" in text
    assert "Один конфиг = одно устройство" in text


def test_extract_links_deduplicates_and_ignores_invalid_values() -> None:
    user = {"links": [" vless://a ", "vless://a", "", 123, "vless://b"]}
    assert bot.extract_links(user) == ["vless://a", "vless://b"]


def test_split_message_respects_limit() -> None:
    text = "\n".join(["line"] * 80)
    parts = bot.split_message(text, limit=60)
    assert len(parts) > 1
    assert all(len(part) <= 60 for part in parts)


def test_parse_sar_dev_output_24h_format() -> None:
    text = """Linux 6.8.0 (host)\t03/22/2026\t_x86_64_\t(2 CPU)
10:00:01 IFACE   rxpck/s txpck/s rxkB/s txkB/s rxcmp/s txcmp/s rxmcst/s
10:00:02 enp0s3  100.00  120.00   10.00   20.00    0.00    0.00     0.00
10:00:03 enp0s3  120.00  130.00   20.00   30.00    0.00    0.00     0.00
Average: enp0s3  110.00  125.00   15.00   25.00    0.00    0.00     0.00
"""
    mbps = bot._parse_sar_dev_output(text, "enp0s3")
    assert mbps is not None
    # ((10+20)+(20+30))/2 = 40 kB/s => 0.3125 Mbps
    assert abs(mbps - 0.3125) < 1e-6


def test_parse_sar_dev_output_ampm_format() -> None:
    text = """Linux 6.8.0 (host)\t03/22/2026\t_x86_64_\t(2 CPU)
10:00:01 AM IFACE   rxpck/s txpck/s rxkB/s txkB/s rxcmp/s txcmp/s rxmcst/s
10:00:02 AM enp0s3 100.00  120.00   12.00   18.00    0.00    0.00     0.00
10:00:03 AM enp0s3 120.00  130.00   18.00   30.00    0.00    0.00     0.00
"""
    mbps = bot._parse_sar_dev_output(text, "enp0s3")
    assert mbps is not None
    # ((12+18)+(18+30))/2 = 39 kB/s => 0.3046875 Mbps
    assert abs(mbps - 0.3046875) < 1e-6


def test_format_last_online_handles_none_and_iso() -> None:
    assert bot.format_last_online(None) == "нет данных"
    formatted = bot.format_last_online("2026-03-23T08:04:46.023092")
    assert "UTC" in formatted
