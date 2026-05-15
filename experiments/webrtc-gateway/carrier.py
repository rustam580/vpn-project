from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from aiohttp import web
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

LOGGER = logging.getLogger("webrtc_gateway.carrier")


@dataclass
class CarrierRuntimeMetrics:
    peer_connections_created: int = 0
    peer_connections_closed: int = 0
    data_channels_opened: int = 0
    data_channels_closed: int = 0
    messages_received: int = 0
    messages_sent: int = 0
    errors: int = 0


class Carrier(Protocol):
    name: str

    async def handle_offer(self, params: dict[str, Any]) -> dict[str, Any]: ...

    async def close(self) -> None: ...

    def metrics_snapshot(self) -> dict[str, Any]: ...


class DirectDataChannelCarrier:
    """Self-hosted browser<->gateway WebRTC carrier.

    This is a lab baseline only. It validates DataChannel mechanics but does
    not exercise the carrier-based whitelist hypothesis.
    """

    name = "direct"

    def __init__(self) -> None:
        self.metrics = CarrierRuntimeMetrics()
        self.peers: set[RTCPeerConnection] = set()

    async def handle_offer(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("type") != "offer" or not params.get("sdp"):
            raise web.HTTPBadRequest(text='{"ok": false, "error": "Expected SDP offer"}', content_type="application/json")

        ice_servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))
        self.peers.add(pc)
        self.metrics.peer_connections_created += 1
        LOGGER.info("direct peer created; active=%s", len(self.peers))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            LOGGER.info("direct connection state changed: %s", pc.connectionState)
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                if pc in self.peers:
                    self.peers.discard(pc)
                    self.metrics.peer_connections_closed += 1
                await pc.close()

        @pc.on("datachannel")
        def on_datachannel(channel: Any) -> None:
            LOGGER.info("direct datachannel created: %s", channel.label)

            @channel.on("open")
            def on_open() -> None:
                self.metrics.data_channels_opened += 1
                LOGGER.info("direct datachannel open: %s", channel.label)

            @channel.on("close")
            def on_close() -> None:
                self.metrics.data_channels_closed += 1
                LOGGER.info("direct datachannel closed: %s", channel.label)

            @channel.on("message")
            def on_message(message: Any) -> None:
                self.metrics.messages_received += 1
                text = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else str(message)
                reply = "pong" if text == "ping" else f"echo:{text}"
                try:
                    channel.send(reply)
                    self.metrics.messages_sent += 1
                except Exception:
                    self.metrics.errors += 1
                    LOGGER.exception("failed to send direct datachannel reply")

        await pc.setRemoteDescription(RTCSessionDescription(sdp=str(params["sdp"]), type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {"ok": True, "sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def close(self) -> None:
        await asyncio.gather(*(pc.close() for pc in list(self.peers)), return_exceptions=True)
        self.peers.clear()

    def metrics_snapshot(self) -> dict[str, Any]:
        payload = asdict(self.metrics)
        payload["carrier"] = self.name
        payload["active_peer_connections"] = len(self.peers)
        return payload
