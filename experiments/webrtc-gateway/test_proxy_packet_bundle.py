from __future__ import annotations

import pytest

from proxy_packet_bundle import ProxyPacketBundleError, decode_proxy_packet_bundle, encode_proxy_packet_bundle


def test_proxy_packet_bundle_roundtrip():
    packets = [b"open", b"data" * 3, b"close"]
    bundle = encode_proxy_packet_bundle(packets)

    assert decode_proxy_packet_bundle(bundle) == packets


def test_proxy_packet_bundle_allows_empty_bundle_for_protocol_tests():
    bundle = encode_proxy_packet_bundle([])

    assert decode_proxy_packet_bundle(bundle) == []


def test_proxy_packet_bundle_rejects_bad_magic():
    bundle = bytearray(encode_proxy_packet_bundle([b"x"]))
    bundle[0:4] = b"BAD!"

    with pytest.raises(ProxyPacketBundleError, match="magic"):
        decode_proxy_packet_bundle(bytes(bundle))


def test_proxy_packet_bundle_rejects_trailing_bytes():
    bundle = encode_proxy_packet_bundle([b"x"]) + b"extra"

    with pytest.raises(ProxyPacketBundleError, match="trailing"):
        decode_proxy_packet_bundle(bundle)
