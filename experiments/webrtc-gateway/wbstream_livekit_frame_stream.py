from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from livekit import rtc

from video_frame_codec import (
    DEFAULT_CELL_SIZE,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    VideoFrameCodecError,
    VideoFrameReassembler,
    encode_payload_frames_rgba,
    max_payload_bytes,
)
from wbstream_api import extract_room_id, probe_room

FPS = 8
DEFAULT_SECRET = "rootvpn-lab-video-carrier-secret"
DEFAULT_MESSAGE = "RootVPN multi-frame video carrier " * 40


@dataclass(frozen=True)
class VideoStreamResult:
    ok: bool
    room_id: str
    server_url: str
    elapsed_ms: int
    publisher_identity: str
    receiver_identity: str
    payload_bytes: int
    received_sha256: str
    frame_width: int
    frame_height: int
    max_payload_per_frame: int
    encoded_frames: int
    chunks_received: int
    decode_attempts: int
    fps: int

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "room_id": self.room_id,
            "server_url": self.server_url,
            "elapsed_ms": self.elapsed_ms,
            "publisher_identity": self.publisher_identity,
            "receiver_identity": self.receiver_identity,
            "payload_bytes": self.payload_bytes,
            "received_sha256": self.received_sha256,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "max_payload_per_frame": self.max_payload_per_frame,
            "encoded_frames": self.encoded_frames,
            "chunks_received": self.chunks_received,
            "decode_attempts": self.decode_attempts,
            "fps": self.fps,
        }


def _sha256_hex(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _participant_identity(room: rtc.Room) -> str:
    try:
        return str(room.local_participant.identity)
    except Exception:
        return ""


async def _wait_for_remote_identity(room: rtc.Room, *, timeout_sec: float) -> str:
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        participants = list(room.remote_participants.values())
        if participants:
            return str(participants[0].identity)
        await asyncio.sleep(0.2)
    raise TimeoutError("remote participant did not appear in the LiveKit room")


def _build_payload(message: str, payload_bytes: int | None) -> bytes:
    if payload_bytes is None:
        return message.encode("utf-8")
    if payload_bytes < 0:
        raise ValueError("payload_bytes must be non-negative")
    seed = message.encode("utf-8") or b"RootVPN"
    return bytes(seed[idx % len(seed)] ^ (idx * 31 % 251) for idx in range(payload_bytes))


async def wbstream_video_stream_probe(
    room: str,
    *,
    message: str = DEFAULT_MESSAGE,
    payload_bytes: int | None = None,
    secret: str = DEFAULT_SECRET,
    timeout_sec: float = 90.0,
    fps: int = FPS,
) -> VideoStreamResult:
    room_id = extract_room_id(room)
    payload = _build_payload(message, payload_bytes)
    frames = encode_payload_frames_rgba(payload, secret=secret)
    started = time.perf_counter()

    details_a, details_b = await asyncio.gather(
        probe_room(room_id, display_name="RootVPN Stream A", timeout_sec=timeout_sec),
        probe_room(room_id, display_name="RootVPN Stream B", timeout_sec=timeout_sec),
    )
    if details_a.server_url != details_b.server_url:
        raise RuntimeError(
            f"WB Stream returned different LiveKit servers: {details_a.server_url} != {details_b.server_url}"
        )

    room_a = rtc.Room()
    room_b = rtc.Room()
    loop = asyncio.get_running_loop()
    received_payload: asyncio.Future[bytes] = loop.create_future()
    reassembler = VideoFrameReassembler(secret=secret)
    stream_tasks: set[asyncio.Task[None]] = set()
    push_task: asyncio.Task[None] | None = None
    decode_attempts = 0

    async def process_video_stream(stream: rtc.VideoStream) -> None:
        nonlocal decode_attempts
        async for event in stream:
            frame = event.frame.convert(rtc.VideoBufferType.RGBA)
            decode_attempts += 1
            try:
                maybe_payload = reassembler.add_frame_rgba(
                    bytes(frame.data),
                    width=frame.width,
                    height=frame.height,
                    cell_size=DEFAULT_CELL_SIZE,
                )
            except VideoFrameCodecError:
                continue
            if maybe_payload is not None and not received_payload.done():
                received_payload.set_result(maybe_payload)
                break

    try:
        @room_b.on("track_subscribed")
        def on_track_subscribed(
            track: rtc.Track,
            _publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            if participant.identity != _participant_identity(room_a):
                return
            task = asyncio.create_task(process_video_stream(rtc.VideoStream(track)))
            stream_tasks.add(task)
            task.add_done_callback(stream_tasks.discard)

        await asyncio.gather(
            room_a.connect(details_a.server_url, details_a.room_token),
            room_b.connect(details_b.server_url, details_b.room_token),
        )
        await _wait_for_remote_identity(room_a, timeout_sec=timeout_sec)
        await _wait_for_remote_identity(room_b, timeout_sec=timeout_sec)

        source = rtc.VideoSource(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT)
        track = rtc.LocalVideoTrack.create_video_track("rootvpn-frame-stream", source)
        opts = rtc.TrackPublishOptions()
        opts.source = rtc.TrackSource.SOURCE_CAMERA
        await room_a.local_participant.publish_track(track, opts)

        async def push_frames() -> None:
            await asyncio.sleep(1.0)
            idx = 0
            while not received_payload.done():
                frame = frames[idx % len(frames)]
                source.capture_frame(
                    rtc.VideoFrame(DEFAULT_WIDTH, DEFAULT_HEIGHT, rtc.VideoBufferType.RGBA, frame)
                )
                idx += 1
                await asyncio.sleep(1.0 / fps)

        push_task = asyncio.create_task(push_frames())
        received = await asyncio.wait_for(received_payload, timeout=timeout_sec)
        if received != payload:
            raise RuntimeError("received payload hash mismatch")

        return VideoStreamResult(
            ok=True,
            room_id=room_id,
            server_url=details_a.server_url,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            publisher_identity=_participant_identity(room_a),
            receiver_identity=_participant_identity(room_b),
            payload_bytes=len(payload),
            received_sha256=_sha256_hex(received),
            frame_width=DEFAULT_WIDTH,
            frame_height=DEFAULT_HEIGHT,
            max_payload_per_frame=max_payload_bytes(),
            encoded_frames=len(frames),
            chunks_received=reassembler.chunks_received,
            decode_attempts=decode_attempts,
            fps=fps,
        )
    finally:
        if push_task is not None:
            push_task.cancel()
        for task in list(stream_tasks):
            task.cancel()
        if room_b.isconnected():
            await room_b.disconnect()
        if room_a.isconnected():
            await room_a.disconnect()


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Send a multi-frame encrypted payload through WB Stream video")
    parser.add_argument("room", help="WB Stream room ID or URL")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--payload-bytes", type=int)
    parser.add_argument("--secret", default=DEFAULT_SECRET)
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()

    result = await wbstream_video_stream_probe(
        args.room,
        message=args.message,
        payload_bytes=args.payload_bytes,
        secret=args.secret,
        timeout_sec=args.timeout_sec,
        fps=args.fps,
    )
    print(json.dumps(result.safe_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
