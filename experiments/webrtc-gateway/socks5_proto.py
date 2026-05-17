from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass

SOCKS_VERSION = 5
NO_AUTH = 0
NO_ACCEPTABLE_METHODS = 0xFF
CMD_CONNECT = 1
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4
REP_SUCCEEDED = 0
REP_GENERAL_FAILURE = 1
REP_COMMAND_NOT_SUPPORTED = 7
REP_ADDRESS_TYPE_NOT_SUPPORTED = 8


class Socks5ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Socks5Greeting:
    methods: tuple[int, ...]
    bytes_consumed: int


@dataclass(frozen=True)
class Socks5ConnectRequest:
    host: str
    port: int
    atyp: int
    bytes_consumed: int


def parse_greeting(data: bytes) -> Socks5Greeting:
    if len(data) < 2:
        raise Socks5ProtocolError("incomplete socks greeting")
    version = data[0]
    if version != SOCKS_VERSION:
        raise Socks5ProtocolError("unsupported socks version")
    nmethods = data[1]
    expected = 2 + nmethods
    if len(data) < expected:
        raise Socks5ProtocolError("incomplete socks methods")
    return Socks5Greeting(methods=tuple(data[2:expected]), bytes_consumed=expected)


def select_auth_method(greeting: Socks5Greeting) -> int:
    return NO_AUTH if NO_AUTH in greeting.methods else NO_ACCEPTABLE_METHODS


def build_method_selection(method: int) -> bytes:
    if not 0 <= method <= 0xFF:
        raise Socks5ProtocolError("method must fit uint8")
    return bytes([SOCKS_VERSION, method])


def parse_connect_request(data: bytes) -> Socks5ConnectRequest:
    if len(data) < 4:
        raise Socks5ProtocolError("incomplete socks request")
    version, command, reserved, atyp = data[:4]
    if version != SOCKS_VERSION:
        raise Socks5ProtocolError("unsupported socks version")
    if reserved != 0:
        raise Socks5ProtocolError("invalid socks reserved byte")
    if command != CMD_CONNECT:
        raise Socks5ProtocolError("only CONNECT is supported")

    if atyp == ATYP_IPV4:
        if len(data) < 10:
            raise Socks5ProtocolError("incomplete ipv4 request")
        host = str(ipaddress.IPv4Address(data[4:8]))
        port = struct.unpack("!H", data[8:10])[0]
        consumed = 10
    elif atyp == ATYP_DOMAIN:
        if len(data) < 5:
            raise Socks5ProtocolError("incomplete domain request")
        host_len = data[4]
        expected = 5 + host_len + 2
        if len(data) < expected:
            raise Socks5ProtocolError("incomplete domain request")
        host = data[5 : 5 + host_len].decode("idna")
        port = struct.unpack("!H", data[5 + host_len : expected])[0]
        consumed = expected
    elif atyp == ATYP_IPV6:
        if len(data) < 22:
            raise Socks5ProtocolError("incomplete ipv6 request")
        host = str(ipaddress.IPv6Address(data[4:20]))
        port = struct.unpack("!H", data[20:22])[0]
        consumed = 22
    else:
        raise Socks5ProtocolError("unsupported address type")

    if port <= 0:
        raise Socks5ProtocolError("invalid target port")
    return Socks5ConnectRequest(host=host, port=port, atyp=atyp, bytes_consumed=consumed)


def build_connect_reply(reply_code: int = REP_SUCCEEDED) -> bytes:
    if not 0 <= reply_code <= 0xFF:
        raise Socks5ProtocolError("reply code must fit uint8")
    # Bind address/port are irrelevant for this lab bridge.
    return bytes([SOCKS_VERSION, reply_code, 0, ATYP_IPV4]) + b"\x00\x00\x00\x00\x00\x00"
