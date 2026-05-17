from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from proxy_packet_bundle import encode_proxy_packet_bundle

CODEC_TILE2 = "tile2"
DEFAULT_SECRET = "rootvpn-lab-video-carrier-secret"
FPS = 8
ACK_FPS = 4
DEFAULT_WINDOW_SIZE = 4
DEFAULT_RETRY_TIMEOUT_SEC = 2.5
DEFAULT_DATA_REPEATS = 1
DEFAULT_CONNECT_ATTEMPTS = 1


class WBStreamProxyCarrierError(RuntimeError):
    pass


async def _call_wbstream_video_window_probe(room: str, **kwargs: Any) -> Any:
    from wbstream_livekit_frame_window import wbstream_video_window_probe

    return await wbstream_video_window_probe(room, **kwargs)


@dataclass(frozen=True)
class WBStreamProxyDeliveryResult:
    ok: bool
    packet_count: int
    bundle_bytes: int
    bundle_sha256: str
    window_result: Any

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "packet_count": self.packet_count,
            "bundle_bytes": self.bundle_bytes,
            "bundle_sha256": self.bundle_sha256,
            "window_result": self.window_result.safe_dict(),
        }


@dataclass(frozen=True)
class WBStreamProxyCarrier:
    """Lab-only proxy packet delivery adapter over WB stream-mode video frames.

    This adapter proves that encoded RootVPN proxy packets can be delivered over
    the current WB `tile2` stream-mode path. It intentionally does not implement
    remote egress yet; a future remote endpoint must decode the bundle, execute
    policy-checked routes, and send a response bundle back.
    """

    room: str
    secret: str = DEFAULT_SECRET
    codec: str = CODEC_TILE2
    fps: int = FPS
    ack_fps: int = ACK_FPS
    window_size: int = DEFAULT_WINDOW_SIZE
    retry_timeout_sec: float = DEFAULT_RETRY_TIMEOUT_SEC
    data_repeats: int = DEFAULT_DATA_REPEATS
    stream_id: int = 7007
    connect_attempts: int = DEFAULT_CONNECT_ATTEMPTS
    timeout_sec: float = 120.0

    async def deliver_packets(self, packets: list[bytes]) -> WBStreamProxyDeliveryResult:
        if not packets:
            raise WBStreamProxyCarrierError("at least one proxy packet is required")
        bundle = encode_proxy_packet_bundle(packets)
        result = await _call_wbstream_video_window_probe(
            self.room,
            payload=bundle,
            secret=self.secret,
            timeout_sec=self.timeout_sec,
            fps=self.fps,
            ack_fps=self.ack_fps,
            window_size=self.window_size,
            retry_timeout_sec=self.retry_timeout_sec,
            codec=self.codec,
            data_repeats=self.data_repeats,
            stream_mode=True,
            stream_id=self.stream_id,
            connect_attempts=self.connect_attempts,
        )
        return WBStreamProxyDeliveryResult(
            ok=result.ok,
            packet_count=len(packets),
            bundle_bytes=len(bundle),
            bundle_sha256=hashlib.sha256(bundle).hexdigest(),
            window_result=result,
        )

    def exchange(self, _packets: list[bytes]) -> list[bytes]:
        raise WBStreamProxyCarrierError(
            "WBStreamProxyCarrier is delivery-only until a remote egress endpoint returns response bundles"
        )
