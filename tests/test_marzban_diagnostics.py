from src.vpnbot.marzban_diagnostics import format_marzban_inbounds_report


def test_format_marzban_inbounds_report_shows_flow_and_tags() -> None:
    inbounds = {
        "vless": [
            {
                "tag": "VLESS Reality",
                "port": 443,
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverNames": ["www.microsoft.com"],
                        "fingerprint": "chrome",
                    },
                },
                "settings": {"clients": [{"flow": "xtls-rprx-vision"}]},
            }
        ]
    }

    text = format_marzban_inbounds_report(
        inbounds,
        protocol="vless",
        config_delivery_mode="subscription_first",
        subscription_public_base_url="https://sub.example",
    )

    assert "VLESS Reality" in text
    assert "xtls-rprx-vision" in text
    assert "www.microsoft.com" in text
    assert "Subscription public base: set" in text


def test_format_marzban_inbounds_report_handles_missing_protocol() -> None:
    text = format_marzban_inbounds_report(
        {"vmess": [{"tag": "VMess"}]},
        protocol="vless",
        config_delivery_mode="direct",
        subscription_public_base_url="",
    )

    assert "vless inbounds visible to bot: 0" in text
    assert "does not see usable inbounds" in text
