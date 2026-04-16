from pathlib import Path
from types import SimpleNamespace

import website_api


REQUIRED_CHECKOUT_DOM_IDS = [
    "year",
    "checkout-form",
    "plan-select",
    "provider-select",
    "contact-input",
    "checkout-error",
    "order-id",
    "order-status",
    "pay-link",
    "check-btn",
    "order-message",
    "delivery-box",
    "sub-url",
    "copy-sub-url",
    "copy-msg",
]

EXPECTED_RU_COPY_SNIPPETS = [
    "Премиальный VPN",
    "Покупка на сайте",
    "Как импортировать подписку",
    "Документы",
    "Частые вопросы",
]


def test_site_js_has_no_broken_placeholder_text() -> None:
    text = Path("site/site.js").read_text(encoding="utf-8")
    assert "????" not in text


def test_site_has_required_checkout_dom_ids() -> None:
    html = Path("site/index.html").read_text(encoding="utf-8")
    for dom_id in REQUIRED_CHECKOUT_DOM_IDS:
        assert f'id="{dom_id}"' in html, f"Missing required DOM id: {dom_id}"


def test_site_copy_contains_expected_russian_headlines() -> None:
    html = Path("site/index.html").read_text(encoding="utf-8")
    for phrase in EXPECTED_RU_COPY_SNIPPETS:
        assert phrase in html, f"Missing expected UI phrase: {phrase}"


def test_site_files_have_no_replacement_character() -> None:
    site_files = [
        Path("site/index.html"),
        Path("site/site.js"),
        Path("site/terms.html"),
        Path("site/privacy.html"),
        Path("site/refund.html"),
        Path("site/autorenew.html"),
    ]
    for path in site_files:
        text = path.read_text(encoding="utf-8")
        assert "�" not in text, f"UTF-8 replacement character found in: {path}"


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
