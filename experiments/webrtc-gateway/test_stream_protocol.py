from __future__ import annotations

import pytest

from stream_protocol import (
    StreamFrame,
    StreamProtocolError,
    StreamReassembler,
    decode_stream_frame,
    encode_stream_frame,
    max_stream_payload_bytes,
    segment_stream_payload,
)
from video_frame_codec import (
    decode_tile2_frame_rgba,
    encode_tile2_frame_rgba,
    tile2_max_payload_bytes,
)

SECRET = "rootvpn-lab-secret-for-tests"


def test_stream_frame_roundtrip():
    packet = encode_stream_frame(
        StreamFrame(stream_id=7, offset=12, payload=b"hello", fin=True),
        max_packet_bytes=64,
    )

    frame = decode_stream_frame(packet)

    assert frame.stream_id == 7
    assert frame.offset == 12
    assert frame.payload == b"hello"
    assert frame.fin is True


def test_stream_frame_capacity_guard():
    with pytest.raises(StreamProtocolError, match="capacity"):
        max_stream_payload_bytes(1)
    with pytest.raises(StreamProtocolError, match="does not fit"):
        encode_stream_frame(StreamFrame(stream_id=1, offset=0, payload=b"x" * 20), max_packet_bytes=24)


def test_stream_reassembler_handles_out_of_order_and_duplicates():
    payload = b"stream payload " * 50
    packets = segment_stream_payload(payload, stream_id=42, max_packet_bytes=80)
    reassembler = StreamReassembler(stream_id=42)

    result = reassembler.add_packet(packets[-1])
    assert result is None
    assert reassembler.add_packet(packets[-1]) is None
    for packet in reversed(packets[:-1]):
        result = reassembler.add_packet(packet)

    assert result == payload
    assert reassembler.segments_received == len(packets)
    assert reassembler.fin_offset == len(payload)


def test_stream_reassembler_waits_for_missing_gap():
    payload = b"abcdef" * 20
    packets = segment_stream_payload(payload, stream_id=1, max_packet_bytes=40)
    reassembler = StreamReassembler(stream_id=1)

    for packet in packets[1:]:
        assert reassembler.add_packet(packet) is None

    assert reassembler.add_packet(packets[0]) == payload


def test_stream_reassembler_rejects_wrong_stream():
    reassembler = StreamReassembler(stream_id=1)
    packet = encode_stream_frame(StreamFrame(stream_id=2, offset=0, payload=b"x", fin=True))

    with pytest.raises(StreamProtocolError, match="stream_id"):
        reassembler.add_packet(packet)


def test_stream_reassembler_rejects_conflicting_duplicate():
    reassembler = StreamReassembler(stream_id=1)
    reassembler.add_frame(StreamFrame(stream_id=1, offset=0, payload=b"abc"))

    with pytest.raises(StreamProtocolError, match="conflicting"):
        reassembler.add_frame(StreamFrame(stream_id=1, offset=0, payload=b"xyz"))


def test_stream_reassembler_rejects_overlap():
    reassembler = StreamReassembler(stream_id=1)
    reassembler.add_frame(StreamFrame(stream_id=1, offset=0, payload=b"abc"))

    with pytest.raises(StreamProtocolError, match="overlapping"):
        reassembler.add_frame(StreamFrame(stream_id=1, offset=2, payload=b"cde"))


def test_stream_protocol_over_tile2_video_frames():
    payload = b"RootVPN byte-stream over tile2 video frames " * 30
    max_packet = tile2_max_payload_bytes()
    stream_packets = segment_stream_payload(payload, stream_id=99, max_packet_bytes=max_packet)
    video_frames = [
        encode_tile2_frame_rgba(packet, secret=SECRET, seq=seq, total=len(stream_packets), nonce=1000 + seq)
        for seq, packet in enumerate(stream_packets)
    ]
    decoded_packets = [
        decode_tile2_frame_rgba(frame, secret=SECRET).payload
        for frame in reversed(video_frames)
    ]
    reassembler = StreamReassembler(stream_id=99)
    result = None
    for packet in decoded_packets:
        result = reassembler.add_packet(packet)

    assert result == payload
    assert len(stream_packets) > 1
