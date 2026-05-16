from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass

MAGIC = b"RVV1"
ACK_MAGIC = b"RVA1"
VERSION = 1
DEFAULT_WIDTH = 320
DEFAULT_HEIGHT = 240
DEFAULT_CELL_SIZE = 8
TAG_SIZE = 16
HEADER_STRUCT = struct.Struct("!4sBHHQH")
HEADER_SIZE = HEADER_STRUCT.size
MIN_KEY_BYTES = 16


class VideoFrameCodecError(ValueError):
    pass


@dataclass(frozen=True)
class AckFrame:
    total: int
    received: frozenset[int]


@dataclass(frozen=True)
class DecodedFrameChunk:
    seq: int
    total: int
    payload: bytes


class VideoFrameReassembler:
    def __init__(self, *, secret: str | bytes) -> None:
        self._secret = secret
        self._chunks: dict[int, bytes] = {}
        self._total: int | None = None

    @property
    def chunks_received(self) -> int:
        return len(self._chunks)

    @property
    def total(self) -> int | None:
        return self._total

    def add_chunk(self, chunk: DecodedFrameChunk) -> bytes | None:
        if self._total is None:
            self._total = chunk.total
        elif self._total != chunk.total:
            raise VideoFrameCodecError("mixed frame totals in stream")
        self._chunks.setdefault(chunk.seq, chunk.payload)
        if len(self._chunks) != self._total:
            return None
        return b"".join(self._chunks[idx] for idx in range(self._total))

    def add_frame_rgba(
        self,
        frame: bytes,
        *,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        cell_size: int = DEFAULT_CELL_SIZE,
    ) -> bytes | None:
        chunk = decode_frame_rgba(frame, secret=self._secret, width=width, height=height, cell_size=cell_size)
        return self.add_chunk(chunk)


def frame_capacity_bytes(
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> int:
    if width <= 0 or height <= 0 or cell_size <= 0:
        raise VideoFrameCodecError("width, height and cell_size must be positive")
    return ((width // cell_size) * (height // cell_size)) // 8


def make_ack_payload(total: int, received: set[int] | frozenset[int]) -> bytes:
    if not 1 <= total <= 0xFFFF:
        raise VideoFrameCodecError("ack total must fit uint16")
    bitmap = bytearray((total + 7) // 8)
    for seq in received:
        if not 0 <= seq < total:
            raise VideoFrameCodecError("ack seq out of range")
        bitmap[seq // 8] |= 1 << (7 - (seq % 8))
    return ACK_MAGIC + total.to_bytes(2, "big") + bytes(bitmap)


def parse_ack_payload(payload: bytes) -> AckFrame:
    if len(payload) < 6 or payload[:4] != ACK_MAGIC:
        raise VideoFrameCodecError("invalid ack payload")
    total = int.from_bytes(payload[4:6], "big")
    if not 1 <= total <= 0xFFFF:
        raise VideoFrameCodecError("invalid ack total")
    bitmap = payload[6:]
    expected_len = (total + 7) // 8
    if len(bitmap) != expected_len:
        raise VideoFrameCodecError("invalid ack bitmap length")
    received: set[int] = set()
    for seq in range(total):
        if bitmap[seq // 8] & (1 << (7 - (seq % 8))):
            received.add(seq)
    return AckFrame(total=total, received=frozenset(received))


def max_payload_bytes(
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> int:
    return frame_capacity_bytes(width=width, height=height, cell_size=cell_size) - HEADER_SIZE - TAG_SIZE


def _key_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, str):
        raw = secret.encode("utf-8")
    else:
        raw = bytes(secret)
    if len(raw) < MIN_KEY_BYTES:
        raise VideoFrameCodecError(f"secret must be at least {MIN_KEY_BYTES} bytes")
    return hashlib.sha256(raw).digest()


def _subkey(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: int, length: int) -> bytes:
    out = bytearray()
    counter = 0
    nonce_bytes = nonce.to_bytes(8, "big")
    while len(out) < length:
        out.extend(hmac.new(key, nonce_bytes + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def _envelope_payload(
    payload: bytes,
    *,
    secret: str | bytes,
    seq: int = 0,
    total: int = 1,
    nonce: int | None = None,
    capacity: int,
) -> bytes:
    if not 0 <= seq <= 0xFFFF:
        raise VideoFrameCodecError("seq must fit uint16")
    if not 1 <= total <= 0xFFFF:
        raise VideoFrameCodecError("total must fit uint16")
    if seq >= total:
        raise VideoFrameCodecError("seq must be lower than total")
    if len(payload) > capacity - HEADER_SIZE - TAG_SIZE:
        raise VideoFrameCodecError("payload does not fit into one video frame")

    master = _key_bytes(secret)
    enc_key = _subkey(master, b"rootvpn-video-frame-enc")
    auth_key = _subkey(master, b"rootvpn-video-frame-auth")
    if nonce is None:
        nonce = int.from_bytes(os.urandom(8), "big")
    header = HEADER_STRUCT.pack(MAGIC, VERSION, seq, total, nonce, len(payload))
    ciphertext = _xor(payload, _keystream(enc_key, nonce, len(payload)))
    tag = hmac.new(auth_key, header + ciphertext, hashlib.sha256).digest()[:TAG_SIZE]
    envelope = header + ciphertext + tag
    return envelope.ljust(capacity, b"\x00")


def _decode_envelope(envelope: bytes, *, secret: str | bytes) -> DecodedFrameChunk:
    if len(envelope) < HEADER_SIZE + TAG_SIZE:
        raise VideoFrameCodecError("frame envelope is too small")
    magic, version, seq, total, nonce, payload_len = HEADER_STRUCT.unpack(envelope[:HEADER_SIZE])
    if magic != MAGIC:
        raise VideoFrameCodecError("frame magic mismatch")
    if version != VERSION:
        raise VideoFrameCodecError("unsupported frame version")
    if payload_len > len(envelope) - HEADER_SIZE - TAG_SIZE:
        raise VideoFrameCodecError("invalid payload length")
    if not 1 <= total <= 0xFFFF or seq >= total:
        raise VideoFrameCodecError("invalid sequence metadata")

    ciphertext_start = HEADER_SIZE
    ciphertext_end = ciphertext_start + payload_len
    ciphertext = envelope[ciphertext_start:ciphertext_end]
    tag = envelope[ciphertext_end : ciphertext_end + TAG_SIZE]
    master = _key_bytes(secret)
    enc_key = _subkey(master, b"rootvpn-video-frame-enc")
    auth_key = _subkey(master, b"rootvpn-video-frame-auth")
    expected = hmac.new(auth_key, envelope[:HEADER_SIZE] + ciphertext, hashlib.sha256).digest()[:TAG_SIZE]
    if not hmac.compare_digest(tag, expected):
        raise VideoFrameCodecError("frame authentication failed")
    payload = _xor(ciphertext, _keystream(enc_key, nonce, payload_len))
    return DecodedFrameChunk(seq=seq, total=total, payload=payload)


def _bytes_to_bits(data: bytes) -> list[int]:
    bits: list[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    return bits


def _bits_to_bytes(bits: list[int]) -> bytes:
    out = bytearray()
    for idx in range(0, len(bits), 8):
        byte = 0
        for bit in bits[idx : idx + 8]:
            byte = (byte << 1) | bit
        out.append(byte)
    return bytes(out)


def encode_frame_rgba(
    payload: bytes,
    *,
    secret: str | bytes,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    cell_size: int = DEFAULT_CELL_SIZE,
    seq: int = 0,
    total: int = 1,
    nonce: int | None = None,
) -> bytes:
    capacity = frame_capacity_bytes(width=width, height=height, cell_size=cell_size)
    envelope = _envelope_payload(payload, secret=secret, seq=seq, total=total, nonce=nonce, capacity=capacity)
    bits = _bytes_to_bits(envelope)
    cols = width // cell_size
    rows = height // cell_size
    frame = bytearray(width * height * 4)

    for y in range(height):
        cell_y = min(y // cell_size, rows - 1)
        for x in range(width):
            cell_x = min(x // cell_size, cols - 1)
            bit_idx = cell_y * cols + cell_x
            value = 255 if bit_idx < len(bits) and bits[bit_idx] else 0
            offset = (y * width + x) * 4
            frame[offset] = value
            frame[offset + 1] = value
            frame[offset + 2] = value
            frame[offset + 3] = 255
    return bytes(frame)


def encode_payload_frames_rgba(
    payload: bytes,
    *,
    secret: str | bytes,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> list[bytes]:
    chunk_size = max_payload_bytes(width=width, height=height, cell_size=cell_size)
    if chunk_size <= 0:
        raise VideoFrameCodecError("frame geometry is too small for payload chunks")
    chunks = [payload[idx : idx + chunk_size] for idx in range(0, len(payload), chunk_size)]
    if not chunks:
        chunks = [b""]
    if len(chunks) > 0xFFFF:
        raise VideoFrameCodecError("payload requires too many frames")
    total = len(chunks)
    return [
        encode_frame_rgba(
            chunk,
            secret=secret,
            width=width,
            height=height,
            cell_size=cell_size,
            seq=seq,
            total=total,
        )
        for seq, chunk in enumerate(chunks)
    ]


def decode_frame_rgba(
    frame: bytes,
    *,
    secret: str | bytes,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> DecodedFrameChunk:
    expected_len = width * height * 4
    if len(frame) != expected_len:
        raise VideoFrameCodecError(f"expected RGBA frame length {expected_len}, got {len(frame)}")
    cols = width // cell_size
    rows = height // cell_size
    capacity = frame_capacity_bytes(width=width, height=height, cell_size=cell_size)
    bits: list[int] = []

    for cell_y in range(rows):
        for cell_x in range(cols):
            total = 0
            count = 0
            y0 = cell_y * cell_size
            x0 = cell_x * cell_size
            for y in range(y0, y0 + cell_size):
                for x in range(x0, x0 + cell_size):
                    offset = (y * width + x) * 4
                    total += frame[offset] + frame[offset + 1] + frame[offset + 2]
                    count += 3
            bits.append(1 if (total / count) >= 128 else 0)

    envelope = _bits_to_bytes(bits[: capacity * 8])
    return _decode_envelope(envelope, secret=secret)
