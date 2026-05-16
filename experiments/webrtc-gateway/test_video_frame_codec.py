from __future__ import annotations

import pytest

from video_frame_codec import (
    DEFAULT_CELL_SIZE,
    DEFAULT_WIDTH,
    VideoFrameCodecError,
    decode_frame_rgba,
    encode_frame_rgba,
    max_payload_bytes,
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
