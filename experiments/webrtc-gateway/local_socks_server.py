from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from local_bridge import InMemoryProxyCarrier
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
    ATYP_DOMAIN,
    ATYP_IPV4,
    ATYP_IPV6,
    NO_AUTH,
    REP_ADDRESS_TYPE_NOT_SUPPORTED,
    REP_COMMAND_NOT_SUPPORTED,
    REP_GENERAL_FAILURE,
    REP_SUCCEEDED,
    SOCKS_VERSION,
    Socks5ConnectRequest,
    Socks5ProtocolError,
    build_connect_reply,
    build_method_selection,
    parse_connect_request,
    parse_greeting,
    select_auth_method,
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class LocalSocksConnectionLog:
    connection_id: int
    target_host: str
    target_port: int
    bytes_in: int
    bytes_out: int


@dataclass
class LocalSocksServer:
    """Local-only SOCKS5 adapter for the WebRTC gateway lab.

    This is intentionally a single-read/single-response harness. It proves that
    browser/app SOCKS bytes can be mapped to RootVPN proxy messages without
    adding public listening, real egress, or production VPN behavior.
    """

    carrier: InMemoryProxyCarrier
    host: str = "127.0.0.1"
    port: int = 0
    max_payload_bytes: int = 64 * 1024
    read_timeout_sec: float = 1.0
    connection_id_factory: Callable[[], int] | None = None
    connection_logs: list[LocalSocksConnectionLog] = field(default_factory=list)
    _server: asyncio.AbstractServer | None = field(default=None, init=False, repr=False)
    _next_connection_id: int = field(default=1, init=False, repr=False)

    async def start(self) -> None:
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError("LocalSocksServer must bind to loopback only")
        if self.max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self._server = await asyncio.start_server(self.handle_client, self.host, self.port)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("server is not started")
        sockname = self._server.sockets[0].getsockname()
        return str(sockname[0]), int(sockname[1])

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection_id = self._allocate_connection_id()
        try:
            method = await self._handle_greeting(reader, writer)
            if method != NO_AUTH:
                return

            target = await self._read_connect_request(reader)
            await self._send_connect_reply(writer, REP_SUCCEEDED)

            try:
                payload = await asyncio.wait_for(reader.read(self.max_payload_bytes), timeout=self.read_timeout_sec)
            except TimeoutError:
                payload = b""
            if not payload:
                return

            response = self._exchange(connection_id=connection_id, target=target, payload=payload)
            if response:
                writer.write(response)
                await writer.drain()
            self.connection_logs.append(
                LocalSocksConnectionLog(
                    connection_id=connection_id,
                    target_host=target.host,
                    target_port=target.port,
                    bytes_in=len(payload),
                    bytes_out=len(response),
                )
            )
        except Socks5ProtocolError as exc:
            await self._send_connect_reply(writer, failure_reply_for_socks_error(exc))
        except asyncio.IncompleteReadError:
            return
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def _handle_greeting(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
        head = await reader.readexactly(2)
        if head[0] != SOCKS_VERSION:
            raise Socks5ProtocolError("unsupported socks version")
        methods = await reader.readexactly(head[1])
        greeting = parse_greeting(head + methods)
        method = select_auth_method(greeting)
        writer.write(build_method_selection(method))
        await writer.drain()
        return method

    async def _read_connect_request(self, reader: asyncio.StreamReader) -> Socks5ConnectRequest:
        head = await reader.readexactly(4)
        atyp = head[3]
        if atyp == ATYP_IPV4:
            tail = await reader.readexactly(6)
        elif atyp == ATYP_DOMAIN:
            host_len = await reader.readexactly(1)
            tail = host_len + await reader.readexactly(host_len[0] + 2)
        elif atyp == ATYP_IPV6:
            tail = await reader.readexactly(18)
        else:
            raise Socks5ProtocolError("unsupported address type")
        return parse_connect_request(head + tail)

    async def _send_connect_reply(self, writer: asyncio.StreamWriter, reply_code: int) -> None:
        writer.write(build_connect_reply(reply_code))
        await writer.drain()

    def _exchange(self, *, connection_id: int, target: Socks5ConnectRequest, payload: bytes) -> bytes:
        outbound: list[ProxyMessage] = [
            ProxyOpen(connection_id=connection_id, host=target.host, port=target.port),
            ProxyData(connection_id=connection_id, sequence=0, payload=payload),
            ProxyClose(connection_id=connection_id),
        ]
        reply_packets = self.carrier.exchange([encode_proxy_message(message) for message in outbound])
        inbound = [decode_proxy_message(packet) for packet in reply_packets]
        errors = [message for message in inbound if isinstance(message, ProxyError)]
        if errors:
            raise Socks5ProtocolError(errors[0].message)
        return b"".join(
            message.payload
            for message in inbound
            if isinstance(message, ProxyData) and message.connection_id == connection_id
        )

    def _allocate_connection_id(self) -> int:
        if self.connection_id_factory is not None:
            return self.connection_id_factory()
        connection_id = self._next_connection_id
        self._next_connection_id += 1
        return connection_id


async def run_local_lab_server(server: LocalSocksServer) -> None:
    """Run until cancelled; useful for manual localhost smoke tests."""

    await server.start()
    try:
        if server._server is None:
            raise RuntimeError("server did not start")
        await server._server.serve_forever()
    except asyncio.CancelledError:
        await server.close()
        raise


def failure_reply_for_socks_error(error: Socks5ProtocolError) -> int:
    message = str(error).lower()
    if "address type" in message:
        return REP_ADDRESS_TYPE_NOT_SUPPORTED
    if "connect" in message or "command" in message:
        return REP_COMMAND_NOT_SUPPORTED
    return REP_GENERAL_FAILURE
