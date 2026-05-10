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
    "VLESS-Reality",
    "Покупка на сайте",
    "Импорт подписки",
    "Частые вопросы",
    "Готовы начать",
]

MOJIBAKE_CODEPOINTS = {
    0x0403,  # Ѓ
    0x0408,  # Ј
    0x0409,  # Љ
    0x040A,  # Њ
    0x040B,  # Ћ
    0x040C,  # Ќ
    0x040F,  # Џ
    0x0453,  # ѓ
    0x0458,  # ј
    0x0459,  # љ
    0x045A,  # њ
    0x045B,  # ћ
    0x045C,  # ќ
    0x045F,  # џ
    0x0491,  # ґ
}


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


def test_website_api_has_no_common_mojibake_codepoints() -> None:
    text = Path("website_api.py").read_text(encoding="utf-8")
    found = sorted({ord(ch) for ch in text if ord(ch) in MOJIBAKE_CODEPOINTS})
    assert not found, f"Possible mojibake codepoints in website_api.py: {found}"


def test_build_delivery_payload_dedupes_links_and_uses_supported_signature(monkeypatch) -> None:
    call_args = {}

    def fake_extract_subscription_links(user, *, public_base_url=""):
        call_args["public_base_url"] = public_base_url
        return ["/sub/token-a", "/sub/token-a", "https://sub.example.com/sub/token-b"]

    def fake_extract_links(_user):
        return ["vless://one", "vless://one", "vless://two"]

    monkeypatch.setattr(website_api, "extract_subscription_links", fake_extract_subscription_links)
    monkeypatch.setattr(website_api, "extract_links", fake_extract_links)

    settings = SimpleNamespace(subscription_public_base_url="https://sub.example.com")
    payload = website_api._build_delivery_payload(settings, {"username": "web_1"})

    assert call_args == {"public_base_url": "https://sub.example.com"}
    assert payload["subscription_links"] == [
        "https://sub.example.com/sub/token-a",
        "https://sub.example.com/sub/token-b",
    ]
    assert payload["subscription_url"] == "https://sub.example.com/sub/token-a"
    assert payload["direct_links"] == ["vless://one", "vless://two"]


def test_extract_subscription_token_from_url_and_raw_token() -> None:
    assert (
        website_api._extract_subscription_token(
            "https://sub.rootvpn.tech:8443/sub/d2ViX2FiYw==?foo=1"
        )
        == "d2ViX2FiYw=="
    )
    assert website_api._extract_subscription_token("d2ViX2FiYw==") == "d2ViX2FiYw=="
    assert website_api._extract_subscription_token("https://example.com/other/path") == ""


def test_build_extend_payload_from_user_extends_from_current_expire() -> None:
    now = 1_700_000_000

    class _FakeTime:
        @staticmethod
        def time() -> int:
            return now

    original_time = website_api.time.time
    website_api.time.time = _FakeTime.time  # type: ignore[assignment]
    try:
        payload = website_api._build_extend_payload_from_user(
            user={"expire": now + 5 * 86400, "data_limit": 0},
            days=30,
            gb=0,
        )
    finally:
        website_api.time.time = original_time  # type: ignore[assignment]

    assert payload["expire"] == now + 35 * 86400
    assert payload["data_limit"] == 0
    assert payload["status"] == "active"
