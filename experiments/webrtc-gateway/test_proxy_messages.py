from __future__ import annotations

import pytest

from proxy_messages import (
    ProxyClose,
    ProxyData,
    ProxyError,
    ProxyMessageError,
    ProxyOpen,
    decode_proxy_message,
    encode_proxy_message,
)
from stream_protocol import StreamReassembler, segment_stream_payload
from video_frame_codec import decode_tile2_frame_rgba, encode_tile2_frame_rgba, tile2_max_payload_bytes

SECRET = "rootvpn-lab-secret-for-tests"


def test_proxy_open_roundtrip():
    message = ProxyOpen(connection_id=10, host="example.com", port=443)
    assert decode_proxy_message(encode_proxy_message(message)) == message


def test_proxy_data_roundtrip():
    message = ProxyData(connection_id=10, sequence=3, payload=b"GET / HTTP/1.1\r\n\r\n")
    assert decode_proxy_message(encode_proxy_message(message)) == message


def test_proxy_close_roundtrip():
    message = ProxyClose(connection_id=10)
    assert decode_proxy_message(encode_proxy_message(message)) == message


def test_proxy_error_roundtrip():
    message = ProxyError(connection_id=10, message="target refused")
    assert decode_proxy_message(encode_proxy_message(message)) == message


def test_proxy_message_rejects_bad_magic():
    packet = bytearray(encode_proxy_message(ProxyClose(connection_id=1)))
    packet[0:4] = b"BAD!"
    with pytest.raises(ProxyMessageError, match="magic"):
        decode_proxy_message(bytes(packet))


def test_proxy_open_rejects_invalid_port():
    with pytest.raises(ProxyMessageError, match="port"):
        encode_proxy_message(ProxyOpen(connection_id=1, host="example.com", port=0))


def test_proxy_messages_over_stream_protocol_and_tile2_frames():
    messages = [
        ProxyOpen(connection_id=7, host="example.com", port=443),
        ProxyData(connection_id=7, sequence=0, payload=b"hello " * 100),
        ProxyClose(connection_id=7),
    ]
    payload = b"".join(encode_proxy_message(message) for message in messages)
    stream_packets = segment_stream_payload(payload, stream_id=7007, max_packet_bytes=tile2_max_payload_bytes())
    video_frames = [
        encode_tile2_frame_rgba(packet, secret=SECRET, seq=seq, total=len(stream_packets), nonce=2000 + seq)
        for seq, packet in enumerate(stream_packets)
    ]
    decoded_packets = [decode_tile2_frame_rgba(frame, secret=SECRET).payload for frame in reversed(video_frames)]
    reassembler = StreamReassembler(stream_id=7007)
    result = None
    for packet in decoded_packets:
        result = reassembler.add_packet(packet)

    assert result == payload
