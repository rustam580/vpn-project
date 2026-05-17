from __future__ import annotations

import pytest

from local_bridge import FakeProxyEgress, InMemoryProxyCarrier
from local_socks_server import LocalSocksConnectionLog, LocalSocksServer
from proxy_messages import ProxyOpen


def _domain_connect(host: bytes = b"example.com", port: int = 443) -> bytes:
    return b"\x05\x01\x00\x03" + bytes([len(host)]) + host + port.to_bytes(2, "big")


async def _open_client(server: LocalSocksServer):
    host, port = server.address
    import asyncio

    return await asyncio.open_connection(host, port)


@pytest.mark.asyncio
async def test_local_socks_server_roundtrips_one_payload():
    seen_targets: list[tuple[str, int]] = []

    def handler(open_message: ProxyOpen, chunks: list[bytes]) -> bytes:
        seen_targets.append((open_message.host, open_message.port))
        return b"echo:" + b"".join(chunks)

    server = LocalSocksServer(
        carrier=InMemoryProxyCarrier(remote=FakeProxyEgress(handler=handler)),
        connection_id_factory=lambda: 77,
    )
    await server.start()
    try:
        reader, writer = await _open_client(server)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x00"

        writer.write(_domain_connect())
        await writer.drain()
        assert await reader.readexactly(10) == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"

        writer.write(b"hello")
        await writer.drain()
        writer.write_eof()

        assert await reader.read() == b"echo:hello"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()

    assert seen_targets == [("example.com", 443)]
    assert server.connection_logs == [
        LocalSocksConnectionLog(
            connection_id=77,
            target_host="example.com",
            target_port=443,
            bytes_in=5,
            bytes_out=10,
        )
    ]


@pytest.mark.asyncio
async def test_local_socks_server_rejects_unsupported_auth_without_carrier_use():
    carrier = InMemoryProxyCarrier(remote=FakeProxyEgress(handler=lambda _open, _chunks: b"unused"))
    server = LocalSocksServer(carrier=carrier)
    await server.start()
    try:
        reader, writer = await _open_client(server)
        writer.write(b"\x05\x01\x02")
        await writer.drain()

        assert await reader.readexactly(2) == b"\x05\xff"
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()

    assert carrier.sent_packets == []


@pytest.mark.asyncio
async def test_local_socks_server_returns_failure_on_remote_error():
    def handler(_open_message: ProxyOpen, _chunks: list[bytes]) -> bytes:
        raise RuntimeError("egress down")

    server = LocalSocksServer(carrier=InMemoryProxyCarrier(remote=FakeProxyEgress(handler=handler)))
    await server.start()
    try:
        reader, writer = await _open_client(server)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x00"

        writer.write(_domain_connect())
        await writer.drain()
        assert await reader.readexactly(10) == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"

        writer.write(b"hello")
        await writer.drain()
        writer.write_eof()

        assert await reader.readexactly(10) == b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00"
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_local_socks_server_requires_loopback_bind():
    server = LocalSocksServer(
        carrier=InMemoryProxyCarrier(remote=FakeProxyEgress(handler=lambda _open, _chunks: b"unused")),
        host="0.0.0.0",
    )
    with pytest.raises(ValueError, match="loopback"):
        await server.start()
