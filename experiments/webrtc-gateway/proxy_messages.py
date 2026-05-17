from __future__ import annotations

import struct
from dataclasses import dataclass

PROXY_MAGIC = b"RVP1"
PROXY_VERSION = 1
MSG_OPEN = 1
MSG_DATA = 2
MSG_CLOSE = 3
MSG_ERROR = 4
OPEN_HEADER_STRUCT = struct.Struct("!4sBBIH")
DATA_HEADER_STRUCT = struct.Struct("!4sBBIIH")
CLOSE_HEADER_STRUCT = struct.Struct("!4sBBI")
ERROR_HEADER_STRUCT = struct.Struct("!4sBBIH")


class ProxyMessageError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyOpen:
    connection_id: int
    host: str
    port: int


@dataclass(frozen=True)
class ProxyData:
    connection_id: int
    sequence: int
    payload: bytes


@dataclass(frozen=True)
class ProxyClose:
    connection_id: int


@dataclass(frozen=True)
class ProxyError:
    connection_id: int
    message: str


ProxyMessage = ProxyOpen | ProxyData | ProxyClose | ProxyError


def encode_proxy_message(message: ProxyMessage) -> bytes:
    if isinstance(message, ProxyOpen):
        _check_connection_id(message.connection_id)
        if not 0 < message.port <= 0xFFFF:
            raise ProxyMessageError("port must fit uint16")
        host_bytes = message.host.encode("idna")
        if not host_bytes or len(host_bytes) > 0xFFFF:
            raise ProxyMessageError("host length must fit uint16")
        return (
            OPEN_HEADER_STRUCT.pack(
                PROXY_MAGIC,
                PROXY_VERSION,
                MSG_OPEN,
                message.connection_id,
                len(host_bytes),
            )
            + host_bytes
            + message.port.to_bytes(2, "big")
        )
    if isinstance(message, ProxyData):
        _check_connection_id(message.connection_id)
        if not 0 <= message.sequence <= 0xFFFFFFFF:
            raise ProxyMessageError("sequence must fit uint32")
        if len(message.payload) > 0xFFFF:
            raise ProxyMessageError("payload length must fit uint16")
        return (
            DATA_HEADER_STRUCT.pack(
                PROXY_MAGIC,
                PROXY_VERSION,
                MSG_DATA,
                message.connection_id,
                message.sequence,
                len(message.payload),
            )
            + message.payload
        )
    if isinstance(message, ProxyClose):
        _check_connection_id(message.connection_id)
        return CLOSE_HEADER_STRUCT.pack(PROXY_MAGIC, PROXY_VERSION, MSG_CLOSE, message.connection_id)
    if isinstance(message, ProxyError):
        _check_connection_id(message.connection_id)
        message_bytes = message.message.encode("utf-8")
        if len(message_bytes) > 0xFFFF:
            raise ProxyMessageError("error message length must fit uint16")
        return (
            ERROR_HEADER_STRUCT.pack(
                PROXY_MAGIC,
                PROXY_VERSION,
                MSG_ERROR,
                message.connection_id,
                len(message_bytes),
            )
            + message_bytes
        )
    raise TypeError(f"unsupported proxy message: {type(message)!r}")


def decode_proxy_message(packet: bytes) -> ProxyMessage:
    if len(packet) < 6:
        raise ProxyMessageError("proxy packet is too small")
    magic = packet[:4]
    version = packet[4]
    message_type = packet[5]
    if magic != PROXY_MAGIC:
        raise ProxyMessageError("proxy magic mismatch")
    if version != PROXY_VERSION:
        raise ProxyMessageError("unsupported proxy version")
    if message_type == MSG_OPEN:
        return _decode_open(packet)
    if message_type == MSG_DATA:
        return _decode_data(packet)
    if message_type == MSG_CLOSE:
        return _decode_close(packet)
    if message_type == MSG_ERROR:
        return _decode_error(packet)
    raise ProxyMessageError("unsupported proxy message type")


def _decode_open(packet: bytes) -> ProxyOpen:
    if len(packet) < OPEN_HEADER_STRUCT.size + 2:
        raise ProxyMessageError("open packet is too small")
    _magic, _version, _message_type, connection_id, host_len = OPEN_HEADER_STRUCT.unpack(
        packet[: OPEN_HEADER_STRUCT.size]
    )
    expected = OPEN_HEADER_STRUCT.size + host_len + 2
    if len(packet) != expected:
        raise ProxyMessageError("invalid open packet length")
    host = packet[OPEN_HEADER_STRUCT.size : OPEN_HEADER_STRUCT.size + host_len].decode("idna")
    port = int.from_bytes(packet[-2:], "big")
    if port <= 0:
        raise ProxyMessageError("invalid open port")
    return ProxyOpen(connection_id=connection_id, host=host, port=port)


def _decode_data(packet: bytes) -> ProxyData:
    if len(packet) < DATA_HEADER_STRUCT.size:
        raise ProxyMessageError("data packet is too small")
    _magic, _version, _message_type, connection_id, sequence, payload_len = DATA_HEADER_STRUCT.unpack(
        packet[: DATA_HEADER_STRUCT.size]
    )
    expected = DATA_HEADER_STRUCT.size + payload_len
    if len(packet) != expected:
        raise ProxyMessageError("invalid data packet length")
    return ProxyData(connection_id=connection_id, sequence=sequence, payload=packet[DATA_HEADER_STRUCT.size:])


def _decode_close(packet: bytes) -> ProxyClose:
    if len(packet) != CLOSE_HEADER_STRUCT.size:
        raise ProxyMessageError("invalid close packet length")
    _magic, _version, _message_type, connection_id = CLOSE_HEADER_STRUCT.unpack(packet)
    return ProxyClose(connection_id=connection_id)


def _decode_error(packet: bytes) -> ProxyError:
    if len(packet) < ERROR_HEADER_STRUCT.size:
        raise ProxyMessageError("error packet is too small")
    _magic, _version, _message_type, connection_id, message_len = ERROR_HEADER_STRUCT.unpack(
        packet[: ERROR_HEADER_STRUCT.size]
    )
    expected = ERROR_HEADER_STRUCT.size + message_len
    if len(packet) != expected:
        raise ProxyMessageError("invalid error packet length")
    message = packet[ERROR_HEADER_STRUCT.size:].decode("utf-8", errors="replace")
    return ProxyError(connection_id=connection_id, message=message)


def _check_connection_id(connection_id: int) -> None:
    if not 0 <= connection_id <= 0xFFFFFFFF:
        raise ProxyMessageError("connection_id must fit uint32")
