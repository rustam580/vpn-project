from pathlib import Path
from types import SimpleNamespace

import website_api


def test_site_js_has_no_broken_placeholder_text() -> None:
    text = Path("site/site.js").read_text(encoding="utf-8")
    assert "????" not in text


def test_build_delivery_payload_dedupes_links_and_uses_supported_signature(monkeypatch) -> None:
    call_args = {}

    def fake_extract_subscription_links(user, *, public_base_url=""):
        call_args["public_base_url"] = public_base_url
        return ["/sub/token-a", "/sub/token-a", "https://sub.example.com/sub/token-b"]

    def fake_extract_links(_user):
        return ["vless://one", "vless://one", "vless://two"]

    monkeypatch.setattr(website_api.bot, "extract_subscription_links", fake_extract_subscription_links)
    monkeypatch.setattr(website_api.bot, "extract_links", fake_extract_links)

    settings = SimpleNamespace(subscription_public_base_url="https://sub.example.com")
    payload = website_api._build_delivery_payload(settings, {"username": "web_1"})

    assert call_args == {"public_base_url": "https://sub.example.com"}
    assert payload["subscription_links"] == [
        "https://sub.example.com/sub/token-a",
        "https://sub.example.com/sub/token-b",
    ]
    assert payload["subscription_url"] == "https://sub.example.com/sub/token-a"
    assert payload["direct_links"] == ["vless://one", "vless://two"]
