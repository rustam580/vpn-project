from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import wbstream_proxy_carrier
from proxy_packet_bundle import decode_proxy_packet_bundle
from wbstream_proxy_carrier import WBStreamProxyCarrier, WBStreamProxyCarrierError


@dataclass(frozen=True)
class FakeWindowResult:
    ok: bool = True

    def safe_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "transport": "fake-wb-window"}


@pytest.mark.asyncio
async def test_wbstream_proxy_carrier_delivers_bundle_with_stream_mode(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_probe(room: str, **kwargs):
        calls.append({"room": room, **kwargs})
        return FakeWindowResult()

    monkeypatch.setattr(wbstream_proxy_carrier, "_call_wbstream_video_window_probe", fake_probe)
    carrier = WBStreamProxyCarrier(room="room-1", stream_id=88, timeout_sec=5.0)

    result = await carrier.deliver_packets([b"packet-a", b"packet-b"])

    assert result.ok is True
    assert result.packet_count == 2
    assert result.bundle_bytes == len(calls[0]["payload"])
    assert decode_proxy_packet_bundle(calls[0]["payload"]) == [b"packet-a", b"packet-b"]
    assert calls[0]["room"] == "room-1"
    assert calls[0]["stream_mode"] is True
    assert calls[0]["stream_id"] == 88
    assert calls[0].get("payload_bytes") is None


@pytest.mark.asyncio
async def test_wbstream_proxy_carrier_rejects_empty_delivery():
    carrier = WBStreamProxyCarrier(room="room-1")

    with pytest.raises(WBStreamProxyCarrierError, match="at least one"):
        await carrier.deliver_packets([])


def test_wbstream_proxy_carrier_exchange_is_not_implemented_until_remote_egress():
    carrier = WBStreamProxyCarrier(room="room-1")

    with pytest.raises(WBStreamProxyCarrierError, match="delivery-only"):
        carrier.exchange([b"packet"])
