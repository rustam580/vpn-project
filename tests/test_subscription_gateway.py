import base64

from subscription_gateway import dedupe_lines, dedupe_subscription_payload


def test_dedupe_lines_preserves_order() -> None:
    unique, raw_count, unique_count = dedupe_lines(
        [" vless://a ", "vless://a", "", "vless://b", "vless://a"]
    )
    assert unique == ["vless://a", "vless://b"]
    assert raw_count == 4
    assert unique_count == 2


def test_dedupe_subscription_payload_base64_body() -> None:
    source = "vless://a\nvless://a\nvless://b\n"
    encoded = base64.b64encode(source.encode("utf-8"))
    payload, raw_count, unique_count = dedupe_subscription_payload(encoded)
    decoded = base64.b64decode(payload).decode("utf-8")
    assert decoded == "vless://a\nvless://b"
    assert raw_count == 3
    assert unique_count == 2


def test_dedupe_subscription_payload_plain_body() -> None:
    payload, raw_count, unique_count = dedupe_subscription_payload(
        b"vless://a\nvless://a\nvless://b\n"
    )
    assert payload.decode("utf-8") == "vless://a\nvless://b"
    assert raw_count == 3
    assert unique_count == 2


def test_dedupe_subscription_payload_passthrough_for_non_subscription() -> None:
    original = b'{"detail":"Not Found"}'
    payload, raw_count, unique_count = dedupe_subscription_payload(original)
    assert payload == original
    assert raw_count == 0
    assert unique_count == 0
