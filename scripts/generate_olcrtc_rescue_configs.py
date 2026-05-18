from __future__ import annotations

import argparse
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_CARRIER = "wbstream"
DEFAULT_TRANSPORT = "vp8channel"
DEFAULT_DNS = "1.1.1.1:53"
DEFAULT_SOCKS_HOST = "127.0.0.1"
DEFAULT_SOCKS_PORT = 8808
DEFAULT_VP8_FPS = 60
DEFAULT_VP8_BATCH = 64
DEFAULT_LIVENESS_INTERVAL = "10s"
DEFAULT_LIVENESS_TIMEOUT = "5s"
DEFAULT_LIVENESS_FAILURES = 3
DEFAULT_MAX_SESSION_DURATION = "2h"
DEFAULT_TRAFFIC_MAX_PAYLOAD_SIZE = 0
DEFAULT_TRAFFIC_MIN_DELAY = "5ms"
DEFAULT_TRAFFIC_MAX_DELAY = "30ms"


@dataclass(frozen=True)
class OlcRtcRescueConfig:
    room_id: str
    key_hex: str
    carrier: str = DEFAULT_CARRIER
    transport: str = DEFAULT_TRANSPORT
    dns: str = DEFAULT_DNS
    socks_host: str = DEFAULT_SOCKS_HOST
    socks_port: int = DEFAULT_SOCKS_PORT
    vp8_fps: int = DEFAULT_VP8_FPS
    vp8_batch: int = DEFAULT_VP8_BATCH
    liveness_interval: str = DEFAULT_LIVENESS_INTERVAL
    liveness_timeout: str = DEFAULT_LIVENESS_TIMEOUT
    liveness_failures: int = DEFAULT_LIVENESS_FAILURES
    max_session_duration: str = DEFAULT_MAX_SESSION_DURATION
    traffic_max_payload_size: int = DEFAULT_TRAFFIC_MAX_PAYLOAD_SIZE
    traffic_min_delay: str = DEFAULT_TRAFFIC_MIN_DELAY
    traffic_max_delay: str = DEFAULT_TRAFFIC_MAX_DELAY
    debug: bool = False

    def normalized(self) -> "OlcRtcRescueConfig":
        return OlcRtcRescueConfig(
            room_id=normalize_room_id(self.room_id, carrier=self.carrier),
            key_hex=self.key_hex,
            carrier=self.carrier,
            transport=self.transport,
            dns=self.dns,
            socks_host=self.socks_host,
            socks_port=self.socks_port,
            vp8_fps=self.vp8_fps,
            vp8_batch=self.vp8_batch,
            liveness_interval=self.liveness_interval,
            liveness_timeout=self.liveness_timeout,
            liveness_failures=self.liveness_failures,
            max_session_duration=self.max_session_duration,
            traffic_max_payload_size=self.traffic_max_payload_size,
            traffic_min_delay=self.traffic_min_delay,
            traffic_max_delay=self.traffic_max_delay,
            debug=self.debug,
        )

    def validate(self) -> None:
        if not self.room_id.strip():
            raise ValueError("room_id is required")
        if len(self.key_hex) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in self.key_hex):
            raise ValueError("key_hex must be 64 hex characters")
        if self.carrier not in {"wbstream", "telemost", "jitsi", "jazz"}:
            raise ValueError("carrier must be one of: wbstream, telemost, jitsi, jazz")
        if self.transport not in {"vp8channel", "datachannel", "seichannel", "videochannel"}:
            raise ValueError("transport must be one of: vp8channel, datachannel, seichannel, videochannel")
        if not 0 < self.socks_port <= 65535:
            raise ValueError("socks_port must fit uint16")
        if self.vp8_fps <= 0:
            raise ValueError("vp8_fps must be positive")
        if self.vp8_batch <= 0:
            raise ValueError("vp8_batch must be positive")


def new_key_hex() -> str:
    return secrets.token_hex(32)


def build_server_yaml(config: OlcRtcRescueConfig) -> str:
    normalized = config.normalized()
    normalized.validate()
    return _common_yaml(normalized, mode="srv")


def build_client_yaml(config: OlcRtcRescueConfig) -> str:
    normalized = config.normalized()
    normalized.validate()
    socks_block = f"""
socks:
  host: {q(normalized.socks_host)}
  port: {normalized.socks_port}
"""
    return _common_yaml(normalized, mode="cnc") + socks_block


def build_uri(config: OlcRtcRescueConfig, *, label: str = "RootVPN Rescue Beta") -> str:
    config = config.normalized()
    config.validate()
    payload = ""
    if config.transport == "vp8channel":
        payload = f"<vp8-fps={config.vp8_fps}&vp8-batch={config.vp8_batch}>"
    return f"olcrtc://{config.carrier}?{config.transport}{payload}@{config.room_id}#{config.key_hex}${label}"


def normalize_room_id(room_id: str, *, carrier: str) -> str:
    room_id = room_id.strip()
    if carrier != "wbstream":
        return room_id
    parsed = urlparse(room_id)
    if parsed.netloc.endswith("stream.wb.ru"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "room":
            return parts[1]
    return room_id


def _common_yaml(config: OlcRtcRescueConfig, *, mode: str) -> str:
    debug = "true" if config.debug else "false"
    return f"""mode: {mode}
link: direct
auth:
  provider: {q(config.carrier)}
room:
  id: {q(config.room_id)}
crypto:
  key: {q(config.key_hex)}
net:
  transport: {q(config.transport)}
  dns: {q(config.dns)}
liveness:
  interval: {config.liveness_interval}
  timeout: {config.liveness_timeout}
  failures: {config.liveness_failures}
lifecycle:
  max_session_duration: {config.max_session_duration}
traffic:
  max_payload_size: {config.traffic_max_payload_size}
  min_delay: {config.traffic_min_delay}
  max_delay: {config.traffic_max_delay}
vp8:
  fps: {config.vp8_fps}
  batch_size: {config.vp8_batch}
data: data
debug: {debug}
"""


def q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_outputs(config: OlcRtcRescueConfig, out_dir: Path, *, label: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "server.yaml").write_text(build_server_yaml(config), encoding="utf-8")
    (out_dir / "client.yaml").write_text(build_client_yaml(config), encoding="utf-8")
    (out_dir / "uri.txt").write_text(build_uri(config, label=label) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate RootVPN olcRTC Rescue Beta lab configs")
    parser.add_argument("room_id", help="WB Stream room id or URL")
    parser.add_argument("--out-dir", default="out/olcrtc-rescue", help="directory for server.yaml/client.yaml/uri.txt")
    parser.add_argument("--key", default="", help="64-hex shared key; generated when omitted")
    parser.add_argument("--carrier", default=DEFAULT_CARRIER)
    parser.add_argument("--transport", default=DEFAULT_TRANSPORT)
    parser.add_argument("--dns", default=DEFAULT_DNS)
    parser.add_argument("--socks-host", default=DEFAULT_SOCKS_HOST)
    parser.add_argument("--socks-port", type=int, default=DEFAULT_SOCKS_PORT)
    parser.add_argument("--vp8-fps", type=int, default=DEFAULT_VP8_FPS)
    parser.add_argument("--vp8-batch", type=int, default=DEFAULT_VP8_BATCH)
    parser.add_argument("--label", default="RootVPN Rescue Beta")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = OlcRtcRescueConfig(
        room_id=args.room_id,
        key_hex=args.key or new_key_hex(),
        carrier=args.carrier,
        transport=args.transport,
        dns=args.dns,
        socks_host=args.socks_host,
        socks_port=args.socks_port,
        vp8_fps=args.vp8_fps,
        vp8_batch=args.vp8_batch,
        debug=args.debug,
    )
    _write_outputs(config, Path(args.out_dir), label=args.label)
    print(f"Wrote olcRTC Rescue Beta configs to {args.out_dir}")
    print(build_uri(config, label=args.label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
