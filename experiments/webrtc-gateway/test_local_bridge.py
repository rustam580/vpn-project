from __future__ import annotations

import pytest

from local_bridge import FakeProxyEgress, InMemoryProxyCarrier, no_acceptable_methods_reply, run_local_socks_exchange
from proxy_messages import ProxyClose, ProxyData, ProxyOpen
from socks5_proto import Socks5ProtocolError


def _domain_connect(host: bytes = b"example.com", port: int = 443) -> bytes:
    return b"\x05\x01\x00\x03" + bytes([len(host)]) + host + port.to_bytes(2, "big")


def test_local_bridge_roundtrip_echoes_via_fake_carrier():
    def handler(open_message: ProxyOpen, chunks: list[bytes]) -> bytes:
        assert open_message.host == "example.com"
        assert open_message.port == 443
        return b"echo:" + b"".join(chunks)

    carrier = InMemoryProxyCarrier(remote=FakeProxyEgress(handler=handler))

    result = run_local_socks_exchange(
        greeting=b"\x05\x01\x00",
        connect_request=_domain_connect(),
        payload=b"hello",
        carrier=carrier,
        connection_id=123,
    )

    assert result.method_reply == b"\x05\x00"
    assert result.connect_reply == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    assert result.response_payload == b"echo:hello"
    assert [type(message) for message in result.outbound_messages] == [ProxyOpen, ProxyData, ProxyClose]
    assert [type(message) for message in result.inbound_messages] == [ProxyData, ProxyClose]
    assert len(carrier.sent_packets) == 3
    assert len(carrier.received_packets) == 2


def test_local_bridge_returns_no_acceptable_method_without_carrier_use():
    carrier = InMemoryProxyCarrier(remote=FakeProxyEgress(handler=lambda _open, _chunks: b"unused"))
    result = run_local_socks_exchange(
        greeting=b"\x05\x01\x02",
        connect_request=_domain_connect(),
        payload=b"hello",
        carrier=carrier,
    )

    assert result.method_reply == no_acceptable_methods_reply()
    assert result.connect_reply == b""
    assert result.response_payload == b""
    assert carrier.sent_packets == []


def test_local_bridge_surfaces_remote_error():
    def handler(_open_message: ProxyOpen, _chunks: list[bytes]) -> bytes:
        raise RuntimeError("egress down")

    carrier = InMemoryProxyCarrier(remote=FakeProxyEgress(handler=handler))

    with pytest.raises(Socks5ProtocolError, match="egress down"):
        run_local_socks_exchange(
            greeting=b"\x05\x01\x00",
            connect_request=_domain_connect(),
            payload=b"hello",
            carrier=carrier,
        )
