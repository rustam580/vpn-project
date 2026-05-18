from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from proxy_messages import (
    ProxyClose,
    ProxyData,
    ProxyError,
    ProxyMessage,
    ProxyOpen,
    decode_proxy_message,
    encode_proxy_message,
)
from proxy_packet_bundle import decode_proxy_packet_bundle, encode_proxy_packet_bundle

EgressHandler = Callable[[ProxyOpen, list[bytes]], bytes]


@dataclass(frozen=True)
class RouteRule:
    host: str
    port: int | None = None

    def matches(self, open_message: ProxyOpen) -> bool:
        expected_host = _normalize_host(self.host)
        actual_host = _normalize_host(open_message.host)
        return expected_host == actual_host and (self.port is None or self.port == open_message.port)


@dataclass(frozen=True)
class RoutePolicy:
    rules: tuple[RouteRule, ...]

    @classmethod
    def allow_exact(cls, host: str, port: int) -> "RoutePolicy":
        return cls((RouteRule(host=host, port=port),))

    def allows(self, open_message: ProxyOpen) -> bool:
        return any(rule.matches(open_message) for rule in self.rules)


@dataclass(frozen=True)
class RemoteProxyEndpointResult:
    response_bundle: bytes
    inbound_messages: list[ProxyMessage]
    outbound_messages: list[ProxyMessage]

    @property
    def response_packets(self) -> list[bytes]:
        return [encode_proxy_message(message) for message in self.outbound_messages]


def handle_proxy_bundle(
    bundle: bytes,
    *,
    policy: RoutePolicy,
    egress: EgressHandler,
) -> RemoteProxyEndpointResult:
    """Decode one RPB1 request bundle and return one RPB1 response bundle.

    This is intentionally a lab-only single-request endpoint. It does not dial
    the network itself; callers provide the egress function, and routes are
    checked before egress is invoked.
    """

    try:
        packets = decode_proxy_packet_bundle(bundle)
        inbound_messages = [decode_proxy_message(packet) for packet in packets]
    except Exception as exc:
        return _result([], [_error(0, f"decode failed: {type(exc).__name__}: {exc}")])

    validation_error = _validate_single_request(inbound_messages)
    if validation_error is not None:
        return _result(inbound_messages, [validation_error])

    open_message = next(message for message in inbound_messages if isinstance(message, ProxyOpen))
    if not policy.allows(open_message):
        return _result(
            inbound_messages,
            [_error(open_message.connection_id, f"route denied: {open_message.host}:{open_message.port}")],
        )

    chunks = [
        message.payload
        for message in sorted(
            (message for message in inbound_messages if isinstance(message, ProxyData)),
            key=lambda message: message.sequence,
        )
    ]
    try:
        response = egress(open_message, chunks)
    except Exception as exc:
        return _result(
            inbound_messages,
            [_error(open_message.connection_id, f"egress failed: {type(exc).__name__}: {exc}")],
        )

    outbound_messages: list[ProxyMessage] = [
        ProxyData(connection_id=open_message.connection_id, sequence=0, payload=response),
        ProxyClose(connection_id=open_message.connection_id),
    ]
    return _result(inbound_messages, outbound_messages)


def _validate_single_request(messages: list[ProxyMessage]) -> ProxyError | None:
    opens = [message for message in messages if isinstance(message, ProxyOpen)]
    if len(opens) != 1:
        return _error(0, "expected exactly one open")
    open_message = opens[0]
    for message in messages:
        connection_id = getattr(message, "connection_id", open_message.connection_id)
        if connection_id != open_message.connection_id:
            return _error(open_message.connection_id, "mixed connection ids in bundle")
        if isinstance(message, ProxyError):
            return _error(open_message.connection_id, f"unexpected client error: {message.message}")
    return None


def _result(inbound: list[ProxyMessage], outbound: list[ProxyMessage]) -> RemoteProxyEndpointResult:
    packets = [encode_proxy_message(message) for message in outbound]
    return RemoteProxyEndpointResult(
        response_bundle=encode_proxy_packet_bundle(packets),
        inbound_messages=inbound,
        outbound_messages=outbound,
    )


def _error(connection_id: int, message: str) -> ProxyError:
    return ProxyError(connection_id=connection_id, message=message[:512])


def _normalize_host(host: str) -> str:
    return host.encode("idna").decode("ascii").lower().rstrip(".")
