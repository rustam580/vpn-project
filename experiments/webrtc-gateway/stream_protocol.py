from __future__ import annotations

import struct
from dataclasses import dataclass

STREAM_MAGIC = b"RVS1"
STREAM_VERSION = 1
FLAG_FIN = 1 << 0
STREAM_HEADER_STRUCT = struct.Struct("!4sBIQBH")
STREAM_HEADER_SIZE = STREAM_HEADER_STRUCT.size


class StreamProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class StreamFrame:
    stream_id: int
    offset: int
    payload: bytes
    fin: bool = False

    @property
    def end_offset(self) -> int:
        return self.offset + len(self.payload)


def max_stream_payload_bytes(max_packet_bytes: int) -> int:
    if max_packet_bytes <= STREAM_HEADER_SIZE:
        raise StreamProtocolError("packet capacity is too small for stream header")
    return max_packet_bytes - STREAM_HEADER_SIZE


def encode_stream_frame(frame: StreamFrame, *, max_packet_bytes: int | None = None) -> bytes:
    if not 0 <= frame.stream_id <= 0xFFFFFFFF:
        raise StreamProtocolError("stream_id must fit uint32")
    if not 0 <= frame.offset <= 0xFFFFFFFFFFFFFFFF:
        raise StreamProtocolError("offset must fit uint64")
    if len(frame.payload) > 0xFFFF:
        raise StreamProtocolError("payload must fit uint16")
    flags = FLAG_FIN if frame.fin else 0
    packet = STREAM_HEADER_STRUCT.pack(
        STREAM_MAGIC,
        STREAM_VERSION,
        frame.stream_id,
        frame.offset,
        flags,
        len(frame.payload),
    ) + frame.payload
    if max_packet_bytes is not None and len(packet) > max_packet_bytes:
        raise StreamProtocolError("stream frame does not fit packet capacity")
    return packet


def decode_stream_frame(packet: bytes) -> StreamFrame:
    if len(packet) < STREAM_HEADER_SIZE:
        raise StreamProtocolError("stream packet is too small")
    magic, version, stream_id, offset, flags, payload_len = STREAM_HEADER_STRUCT.unpack(
        packet[:STREAM_HEADER_SIZE]
    )
    if magic != STREAM_MAGIC:
        raise StreamProtocolError("stream magic mismatch")
    if version != STREAM_VERSION:
        raise StreamProtocolError("unsupported stream version")
    if flags & ~FLAG_FIN:
        raise StreamProtocolError("unsupported stream flags")
    if len(packet) != STREAM_HEADER_SIZE + payload_len:
        raise StreamProtocolError("invalid stream payload length")
    payload = packet[STREAM_HEADER_SIZE:]
    return StreamFrame(stream_id=stream_id, offset=offset, payload=payload, fin=bool(flags & FLAG_FIN))


def segment_stream_payload(
    payload: bytes,
    *,
    stream_id: int,
    max_packet_bytes: int,
) -> list[bytes]:
    chunk_size = max_stream_payload_bytes(max_packet_bytes)
    if not payload:
        return [
            encode_stream_frame(
                StreamFrame(stream_id=stream_id, offset=0, payload=b"", fin=True),
                max_packet_bytes=max_packet_bytes,
            )
        ]

    packets: list[bytes] = []
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + chunk_size]
        fin = offset + len(chunk) >= len(payload)
        packets.append(
            encode_stream_frame(
                StreamFrame(stream_id=stream_id, offset=offset, payload=chunk, fin=fin),
                max_packet_bytes=max_packet_bytes,
            )
        )
        offset += len(chunk)
    return packets


class StreamReassembler:
    def __init__(self, *, stream_id: int) -> None:
        if not 0 <= stream_id <= 0xFFFFFFFF:
            raise StreamProtocolError("stream_id must fit uint32")
        self.stream_id = stream_id
        self._segments: dict[int, bytes] = {}
        self._fin_offset: int | None = None

    @property
    def segments_received(self) -> int:
        return len(self._segments)

    @property
    def fin_offset(self) -> int | None:
        return self._fin_offset

    def add_packet(self, packet: bytes) -> bytes | None:
        return self.add_frame(decode_stream_frame(packet))

    def add_frame(self, frame: StreamFrame) -> bytes | None:
        if frame.stream_id != self.stream_id:
            raise StreamProtocolError("stream_id mismatch")
        if frame.fin:
            fin_offset = frame.end_offset
            if self._fin_offset is None:
                self._fin_offset = fin_offset
            elif self._fin_offset != fin_offset:
                raise StreamProtocolError("conflicting stream fin offset")

        existing = self._segments.get(frame.offset)
        if existing is not None:
            if existing != frame.payload:
                raise StreamProtocolError("conflicting duplicate stream segment")
            return self.try_reassemble()

        self._check_overlap(frame)
        self._segments[frame.offset] = frame.payload
        return self.try_reassemble()

    def try_reassemble(self) -> bytes | None:
        if self._fin_offset is None:
            return None
        out = bytearray()
        offset = 0
        while offset < self._fin_offset:
            segment = self._segments.get(offset)
            if segment is None:
                return None
            out.extend(segment)
            offset += len(segment)
        return bytes(out)

    def _check_overlap(self, frame: StreamFrame) -> None:
        start = frame.offset
        end = frame.end_offset
        for other_start, other_payload in self._segments.items():
            other_end = other_start + len(other_payload)
            if start < other_end and other_start < end:
                raise StreamProtocolError("overlapping stream segment")
