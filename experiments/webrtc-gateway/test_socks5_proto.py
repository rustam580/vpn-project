from __future__ import annotations

import pytest

from socks5_proto import (
    NO_ACCEPTABLE_METHODS,
    NO_AUTH,
    REP_SUCCEEDED,
    Socks5ProtocolError,
    build_connect_reply,
    build_method_selection,
    parse_connect_request,
    parse_greeting,
    select_auth_method,
)


def test_parse_greeting_selects_no_auth():
    greeting = parse_greeting(bytes([5, 2, 2, 0]))
    assert greeting.methods == (2, 0)
    assert greeting.bytes_consumed == 4
    assert select_auth_method(greeting) == NO_AUTH
    assert build_method_selection(NO_AUTH) == b"\x05\x00"


def test_parse_greeting_rejects_unsupported_methods():
    greeting = parse_greeting(bytes([5, 1, 2]))
    assert select_auth_method(greeting) == NO_ACCEPTABLE_METHODS


def test_parse_domain_connect_request():
    request = b"\x05\x01\x00\x03" + bytes([11]) + b"example.com" + (443).to_bytes(2, "big")
    parsed = parse_connect_request(request)
    assert parsed.host == "example.com"
    assert parsed.port == 443
    assert parsed.bytes_consumed == len(request)


def test_parse_ipv4_connect_request():
    request = b"\x05\x01\x00\x01\x7f\x00\x00\x01" + (8080).to_bytes(2, "big")
    parsed = parse_connect_request(request)
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 8080


def test_parse_ipv6_connect_request():
    request = b"\x05\x01\x00\x04" + bytes.fromhex("20010db8000000000000000000000001") + (443).to_bytes(2, "big")
    parsed = parse_connect_request(request)
    assert parsed.host == "2001:db8::1"
    assert parsed.port == 443


def test_connect_reply_success_shape():
    assert build_connect_reply(REP_SUCCEEDED) == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"


def test_parse_connect_request_rejects_bind_command():
    with pytest.raises(Socks5ProtocolError, match="CONNECT"):
        parse_connect_request(b"\x05\x02\x00\x01\x7f\x00\x00\x01\x00\x50")
