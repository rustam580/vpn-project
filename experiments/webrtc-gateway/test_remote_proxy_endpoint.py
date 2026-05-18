from __future__ import annotations

from proxy_messages import ProxyClose, ProxyData, ProxyError, ProxyOpen, decode_proxy_message, encode_proxy_message
from proxy_packet_bundle import decode_proxy_packet_bundle, encode_proxy_packet_bundle
from remote_proxy_endpoint import RoutePolicy, RouteRule, handle_proxy_bundle


def _bundle(*messages):
    return encode_proxy_packet_bundle([encode_proxy_message(message) for message in messages])


def _messages(bundle: bytes):
    return [decode_proxy_message(packet) for packet in decode_proxy_packet_bundle(bundle)]


def test_remote_proxy_endpoint_roundtrips_allowed_route():
    def egress(open_message: ProxyOpen, chunks: list[bytes]) -> bytes:
        assert open_message.host == "example.com"
        assert open_message.port == 443
        return b"echo:" + b"".join(chunks)

    result = handle_proxy_bundle(
        _bundle(
            ProxyOpen(connection_id=7, host="example.com", port=443),
            ProxyData(connection_id=7, sequence=1, payload=b"world"),
            ProxyData(connection_id=7, sequence=0, payload=b"hello "),
            ProxyClose(connection_id=7),
        ),
        policy=RoutePolicy.allow_exact("example.com", 443),
        egress=egress,
    )

    messages = _messages(result.response_bundle)
    assert messages == [
        ProxyData(connection_id=7, sequence=0, payload=b"echo:hello world"),
        ProxyClose(connection_id=7),
    ]
    assert result.outbound_messages == messages


def test_remote_proxy_endpoint_denies_unlisted_route_without_egress_call():
    called = False

    def egress(_open_message: ProxyOpen, _chunks: list[bytes]) -> bytes:
        nonlocal called
        called = True
        return b"unexpected"

    result = handle_proxy_bundle(
        _bundle(ProxyOpen(connection_id=9, host="blocked.example", port=443)),
        policy=RoutePolicy.allow_exact("example.com", 443),
        egress=egress,
    )

    assert called is False
    messages = _messages(result.response_bundle)
    assert isinstance(messages[0], ProxyError)
    assert messages[0].connection_id == 9
    assert "route denied" in messages[0].message


def test_remote_proxy_endpoint_allows_idna_and_case_normalized_host():
    def egress(_open_message: ProxyOpen, chunks: list[bytes]) -> bytes:
        return b"".join(chunks)

    result = handle_proxy_bundle(
        _bundle(
            ProxyOpen(connection_id=3, host="EXAMPLE.COM.", port=443),
            ProxyData(connection_id=3, sequence=0, payload=b"ok"),
        ),
        policy=RoutePolicy((RouteRule(host="example.com", port=443),)),
        egress=egress,
    )

    assert _messages(result.response_bundle)[0] == ProxyData(connection_id=3, sequence=0, payload=b"ok")


def test_remote_proxy_endpoint_rejects_malformed_bundle():
    result = handle_proxy_bundle(
        b"not-rpb1",
        policy=RoutePolicy.allow_exact("example.com", 443),
        egress=lambda _open, _chunks: b"unused",
    )

    messages = _messages(result.response_bundle)
    assert isinstance(messages[0], ProxyError)
    assert messages[0].connection_id == 0
    assert "decode failed" in messages[0].message


def test_remote_proxy_endpoint_requires_exactly_one_open():
    result = handle_proxy_bundle(
        _bundle(ProxyData(connection_id=1, sequence=0, payload=b"x")),
        policy=RoutePolicy.allow_exact("example.com", 443),
        egress=lambda _open, _chunks: b"unused",
    )

    messages = _messages(result.response_bundle)
    assert isinstance(messages[0], ProxyError)
    assert messages[0].connection_id == 0
    assert "expected exactly one open" in messages[0].message


def test_remote_proxy_endpoint_rejects_mixed_connection_ids():
    result = handle_proxy_bundle(
        _bundle(
            ProxyOpen(connection_id=1, host="example.com", port=443),
            ProxyData(connection_id=2, sequence=0, payload=b"x"),
        ),
        policy=RoutePolicy.allow_exact("example.com", 443),
        egress=lambda _open, _chunks: b"unused",
    )

    messages = _messages(result.response_bundle)
    assert isinstance(messages[0], ProxyError)
    assert messages[0].connection_id == 1
    assert "mixed connection ids" in messages[0].message


def test_remote_proxy_endpoint_surfaces_egress_failure():
    def egress(_open_message: ProxyOpen, _chunks: list[bytes]) -> bytes:
        raise RuntimeError("fake egress down")

    result = handle_proxy_bundle(
        _bundle(ProxyOpen(connection_id=5, host="example.com", port=443)),
        policy=RoutePolicy.allow_exact("example.com", 443),
        egress=egress,
    )

    messages = _messages(result.response_bundle)
    assert isinstance(messages[0], ProxyError)
    assert messages[0].connection_id == 5
    assert "fake egress down" in messages[0].message
