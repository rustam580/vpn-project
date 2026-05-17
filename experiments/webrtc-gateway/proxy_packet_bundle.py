from __future__ import annotations

import struct

PROXY_BUNDLE_MAGIC = b"RPB1"
PROXY_BUNDLE_VERSION = 1
PROXY_BUNDLE_HEADER = struct.Struct("!4sBH")
PROXY_BUNDLE_ITEM_HEADER = struct.Struct("!I")
MAX_BUNDLE_PACKETS = 0xFFFF
MAX_BUNDLE_PACKET_BYTES = 0xFFFFFFFF


class ProxyPacketBundleError(ValueError):
    pass


def encode_proxy_packet_bundle(packets: list[bytes]) -> bytes:
    if len(packets) > MAX_BUNDLE_PACKETS:
        raise ProxyPacketBundleError("too many proxy packets in bundle")
    out = bytearray(PROXY_BUNDLE_HEADER.pack(PROXY_BUNDLE_MAGIC, PROXY_BUNDLE_VERSION, len(packets)))
    for packet in packets:
        if len(packet) > MAX_BUNDLE_PACKET_BYTES:
            raise ProxyPacketBundleError("proxy packet is too large")
        out.extend(PROXY_BUNDLE_ITEM_HEADER.pack(len(packet)))
        out.extend(packet)
    return bytes(out)


def decode_proxy_packet_bundle(bundle: bytes) -> list[bytes]:
    if len(bundle) < PROXY_BUNDLE_HEADER.size:
        raise ProxyPacketBundleError("proxy packet bundle is too small")
    magic, version, count = PROXY_BUNDLE_HEADER.unpack(bundle[: PROXY_BUNDLE_HEADER.size])
    if magic != PROXY_BUNDLE_MAGIC:
        raise ProxyPacketBundleError("proxy packet bundle magic mismatch")
    if version != PROXY_BUNDLE_VERSION:
        raise ProxyPacketBundleError("unsupported proxy packet bundle version")
    offset = PROXY_BUNDLE_HEADER.size
    packets: list[bytes] = []
    for _idx in range(count):
        if len(bundle) < offset + PROXY_BUNDLE_ITEM_HEADER.size:
            raise ProxyPacketBundleError("truncated proxy packet bundle item header")
        (packet_len,) = PROXY_BUNDLE_ITEM_HEADER.unpack(bundle[offset : offset + PROXY_BUNDLE_ITEM_HEADER.size])
        offset += PROXY_BUNDLE_ITEM_HEADER.size
        if len(bundle) < offset + packet_len:
            raise ProxyPacketBundleError("truncated proxy packet bundle item")
        packets.append(bundle[offset : offset + packet_len])
        offset += packet_len
    if offset != len(bundle):
        raise ProxyPacketBundleError("trailing bytes in proxy packet bundle")
    return packets
