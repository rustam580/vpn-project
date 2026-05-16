from __future__ import annotations

import pytest

from video_frame_codec import (
    DEFAULT_CELL_SIZE,
    DEFAULT_WIDTH,
    VideoFrameReassembler,
    VideoFrameCodecError,
    decode_frame_rgba,
    encode_frame_rgba,
    encode_payload_frames_rgba,
    make_ack_payload,
    max_payload_bytes,
    parse_ack_payload,
)

SECRET = "rootvpn-lab-secret-for-tests"


def test_video_frame_codec_roundtrip():
    payload = b"hello over video frame"
    frame = encode_frame_rgba(payload, secret=SECRET, nonce=123)
    decoded = decode_frame_rgba(frame, secret=SECRET)
    assert decoded.seq == 0
    assert decoded.total == 1
    assert decoded.payload == payload


def test_video_frame_codec_rejects_wrong_secret():
    frame = encode_frame_rgba(b"secret", secret=SECRET, nonce=123)
    with pytest.raises(VideoFrameCodecError, match="authentication"):
        decode_frame_rgba(frame, secret="wrong-secret-but-long-enough")


def test_video_frame_codec_rejects_tampered_frame():
    frame = bytearray(encode_frame_rgba(b"secret", secret=SECRET, nonce=123))
    # Flip the first encoded bit cell so the authenticated envelope becomes invalid.
    for y in range(DEFAULT_CELL_SIZE):
        for x in range(DEFAULT_CELL_SIZE):
            offset = (y * DEFAULT_WIDTH + x) * 4
            frame[offset] = 255 - frame[offset]
            frame[offset + 1] = 255 - frame[offset + 1]
            frame[offset + 2] = 255 - frame[offset + 2]
    with pytest.raises(VideoFrameCodecError):
        decode_frame_rgba(bytes(frame), secret=SECRET)


def test_video_frame_codec_capacity_guard():
    too_large = b"x" * (max_payload_bytes() + 1)
    with pytest.raises(VideoFrameCodecError, match="does not fit"):
        encode_frame_rgba(too_large, secret=SECRET)


def test_video_frame_codec_survives_small_pixel_noise():
    frame = bytearray(encode_frame_rgba(b"noise tolerant", secret=SECRET, nonce=123))
    # Change a few pixels inside cells; cell-level majority decoding should still recover the frame.
    for offset in range(0, 20 * DEFAULT_CELL_SIZE * 4, DEFAULT_CELL_SIZE * 4):
        frame[offset] = 127
        frame[offset + 1] = 127
        frame[offset + 2] = 127
    decoded = decode_frame_rgba(bytes(frame), secret=SECRET)
    assert decoded.payload == b"noise tolerant"


def test_video_frame_reassembler_roundtrip_out_of_order_with_duplicates():
    payload = b"chunked payload " * 40
    frames = encode_payload_frames_rgba(payload, secret=SECRET)
    assert len(frames) > 1
    reassembler = VideoFrameReassembler(secret=SECRET)

    assert reassembler.add_frame_rgba(frames[1]) is None
    assert reassembler.add_frame_rgba(frames[1]) is None
    result = reassembler.add_frame_rgba(frames[0])
    for frame in frames[2:]:
        result = reassembler.add_frame_rgba(frame)

    assert result == payload
    assert reassembler.chunks_received == len(frames)
    assert reassembler.total == len(frames)


def test_video_frame_reassembler_rejects_mixed_totals():
    first = encode_frame_rgba(b"a", secret=SECRET, seq=0, total=2, nonce=1)
    second = encode_frame_rgba(b"b", secret=SECRET, seq=0, total=1, nonce=2)
    reassembler = VideoFrameReassembler(secret=SECRET)
    assert reassembler.add_frame_rgba(first) is None
    with pytest.raises(VideoFrameCodecError, match="mixed"):
        reassembler.add_frame_rgba(second)


def test_ack_payload_roundtrip():
    ack = parse_ack_payload(make_ack_payload(10, {0, 2, 9}))
    assert ack.total == 10
    assert ack.received == frozenset({0, 2, 9})


def test_ack_payload_rejects_out_of_range_seq():
    with pytest.raises(VideoFrameCodecError, match="out of range"):
        make_ack_payload(3, {3})


def test_ack_payload_can_be_sent_as_video_frame():
    payload = make_ack_payload(9, {0, 1, 8})
    frame = encode_frame_rgba(payload, secret=SECRET, nonce=456)
    decoded = decode_frame_rgba(frame, secret=SECRET)
    ack = parse_ack_payload(decoded.payload)
    assert ack.received == frozenset({0, 1, 8})
