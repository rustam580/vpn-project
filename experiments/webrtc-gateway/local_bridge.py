from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from proxy_messages import (
    ProxyClose,
    ProxyData,
    ProxyError,
    ProxyMessage,
    ProxyOpen,
    decode_proxy_message,
    encode_proxy_message,
)
from socks5_proto import (
    NO_ACCEPTABLE_METHODS,
    NO_AUTH,
    Socks5ProtocolError,
    build_connect_reply,
    build_method_selection,
    parse_connect_request,
    parse_greeting,
    select_auth_method,
)

EgressHandler = Callable[[ProxyOpen, list[bytes]], bytes]


@dataclass(frozen=True)
class LocalBridgeResult:
    method_reply: bytes
    connect_reply: bytes
    response_payload: bytes
    outbound_messages: list[ProxyMessage]
    inbound_messages: list[ProxyMessage]


@dataclass
class InMemoryProxyCarrier:
    remote: "FakeProxyEgress"
    sent_packets: list[bytes] = field(default_factory=list)
    received_packets: list[bytes] = field(default_factory=list)

    def exchange(self, packets: list[bytes]) -> list[bytes]:
        self.sent_packets.extend(packets)
        replies = self.remote.handle_packets(packets)
        self.received_packets.extend(replies)
        return replies


@dataclass
class FakeProxyEgress:
    handler: EgressHandler

    def handle_packets(self, packets: list[bytes]) -> list[bytes]:
        messages = [decode_proxy_message(packet) for packet in packets]
        opens = [message for message in messages if isinstance(message, ProxyOpen)]
        if len(opens) != 1:
            return [encode_proxy_message(ProxyError(connection_id=0, message="expected one open"))]
        open_message = opens[0]
        chunks = [
            message.payload
            for message in messages
            if isinstance(message, ProxyData) and message.connection_id == open_message.connection_id
        ]
        try:
            response = self.handler(open_message, chunks)
        except Exception as exc:
            return [
                encode_proxy_message(
                    ProxyError(connection_id=open_message.connection_id, message=f"{type(exc).__name__}: {exc}")
                )
            ]
        return [
            encode_proxy_message(ProxyData(connection_id=open_message.connection_id, sequence=0, payload=response)),
            encode_proxy_message(ProxyClose(connection_id=open_message.connection_id)),
        ]


def run_local_socks_exchange(
    *,
    greeting: bytes,
    connect_request: bytes,
    payload: bytes,
    carrier: InMemoryProxyCarrier,
    connection_id: int = 1,
) -> LocalBridgeResult:
    parsed_greeting = parse_greeting(greeting)
    method = select_auth_method(parsed_greeting)
    method_reply = build_method_selection(method)
    if method != NO_AUTH:
        return LocalBridgeResult(
            method_reply=method_reply,
            connect_reply=b"",
            response_payload=b"",
            outbound_messages=[],
            inbound_messages=[],
        )

    try:
        target = parse_connect_request(connect_request)
    except Socks5ProtocolError:
        raise

    outbound_messages: list[ProxyMessage] = [
        ProxyOpen(connection_id=connection_id, host=target.host, port=target.port),
        ProxyData(connection_id=connection_id, sequence=0, payload=payload),
        ProxyClose(connection_id=connection_id),
    ]
    reply_packets = carrier.exchange([encode_proxy_message(message) for message in outbound_messages])
    inbound_messages = [decode_proxy_message(packet) for packet in reply_packets]
    errors = [message for message in inbound_messages if isinstance(message, ProxyError)]
    if errors:
        raise Socks5ProtocolError(errors[0].message)
    response = b"".join(
        message.payload
        for message in inbound_messages
        if isinstance(message, ProxyData) and message.connection_id == connection_id
    )
    return LocalBridgeResult(
        method_reply=method_reply,
        connect_reply=build_connect_reply(),
        response_payload=response,
        outbound_messages=outbound_messages,
        inbound_messages=inbound_messages,
    )


def no_acceptable_methods_reply() -> bytes:
    return build_method_selection(NO_ACCEPTABLE_METHODS)
