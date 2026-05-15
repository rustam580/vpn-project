from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aiohttp import web
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
LOGGER = logging.getLogger("webrtc_gateway")


@dataclass
class Metrics:
    peer_connections_created: int = 0
    peer_connections_closed: int = 0
    data_channels_opened: int = 0
    data_channels_closed: int = 0
    messages_received: int = 0
    messages_sent: int = 0
    errors: int = 0
    started_at: int = 0


class GatewayState:
    def __init__(self) -> None:
        self.metrics = Metrics(started_at=int(time.time()))
        self.peers: set[RTCPeerConnection] = set()

    async def close(self) -> None:
        await asyncio.gather(*(pc.close() for pc in list(self.peers)), return_exceptions=True)
        self.peers.clear()


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def metrics(request: web.Request) -> web.Response:
    state: GatewayState = request.app["state"]
    payload = asdict(state.metrics)
    payload["active_peer_connections"] = len(state.peers)
    return web.json_response(payload)


async def offer(request: web.Request) -> web.Response:
    state: GatewayState = request.app["state"]
    params: dict[str, Any] = await request.json()
    if params.get("type") != "offer" or not params.get("sdp"):
        return web.json_response({"ok": False, "error": "Expected SDP offer"}, status=400)

    ice_servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))
    state.peers.add(pc)
    state.metrics.peer_connections_created += 1
    LOGGER.info("peer created; active=%s", len(state.peers))

    @pc.on("connectionstatechange")
    async def on_connectionstatechange() -> None:
        LOGGER.info("connection state changed: %s", pc.connectionState)
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            if pc in state.peers:
                state.peers.discard(pc)
                state.metrics.peer_connections_closed += 1
            await pc.close()

    @pc.on("datachannel")
    def on_datachannel(channel: Any) -> None:
        LOGGER.info("datachannel created: %s", channel.label)

        @channel.on("open")
        def on_open() -> None:
            state.metrics.data_channels_opened += 1
            LOGGER.info("datachannel open: %s", channel.label)

        @channel.on("close")
        def on_close() -> None:
            state.metrics.data_channels_closed += 1
            LOGGER.info("datachannel closed: %s", channel.label)

        @channel.on("message")
        def on_message(message: Any) -> None:
            state.metrics.messages_received += 1
            text = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else str(message)
            reply = "pong" if text == "ping" else f"echo:{text}"
            try:
                channel.send(reply)
                state.metrics.messages_sent += 1
            except Exception:
                state.metrics.errors += 1
                LOGGER.exception("failed to send datachannel reply")

    await pc.setRemoteDescription(RTCSessionDescription(sdp=str(params["sdp"]), type="offer"))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.json_response({"ok": True, "sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def on_shutdown(app: web.Application) -> None:
    state: GatewayState = app["state"]
    await state.close()


def build_app() -> web.Application:
    app = web.Application()
    app["state"] = GatewayState()
    app.router.add_get("/", index)
    app.router.add_get("/metrics", metrics)
    app.router.add_post("/offer", offer)
    app.on_shutdown.append(on_shutdown)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="RootVPN WebRTC gateway experiment")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOGGER.info("starting WebRTC gateway experiment on http://%s:%s", args.host, args.port)
    web.run_app(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
