from __future__ import annotations

import argparse
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from carrier import Carrier, DirectDataChannelCarrier

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
LOGGER = logging.getLogger("webrtc_gateway")


@dataclass
class Metrics:
    started_at: int = 0


class GatewayState:
    def __init__(self, *, carrier: Carrier) -> None:
        self.metrics = Metrics(started_at=int(time.time()))
        self.carrier = carrier

    async def close(self) -> None:
        await self.carrier.close()


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def metrics(request: web.Request) -> web.Response:
    state: GatewayState = request.app["state"]
    payload = asdict(state.metrics)
    payload.update(state.carrier.metrics_snapshot())
    return web.json_response(payload)


async def offer(request: web.Request) -> web.Response:
    state: GatewayState = request.app["state"]
    params: dict[str, Any] = await request.json()
    try:
        payload = await state.carrier.handle_offer(params)
    except web.HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("carrier offer failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    return web.json_response(payload)


async def on_shutdown(app: web.Application) -> None:
    state: GatewayState = app["state"]
    await state.close()


def build_app(*, carrier: Carrier | None = None) -> web.Application:
    app = web.Application()
    app["state"] = GatewayState(carrier=carrier or DirectDataChannelCarrier())
    app.router.add_get("/", index)
    app.router.add_get("/metrics", metrics)
    app.router.add_post("/offer", offer)
    app.on_shutdown.append(on_shutdown)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="RootVPN WebRTC gateway experiment")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--carrier",
        choices=["direct"],
        default="direct",
        help="carrier adapter to use; direct is a lab baseline, not whitelist-resilient",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOGGER.info(
        "starting WebRTC gateway experiment on http://%s:%s carrier=%s",
        args.host,
        args.port,
        args.carrier,
    )
    web.run_app(build_app(carrier=DirectDataChannelCarrier()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
