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
    decode_frame_rgba,
    encode_frame_rgba,
    max_payload_bytes,
)
from wbstream_api import extract_room_id, probe_room

FPS = 8
DEFAULT_SECRET = "rootvpn-lab-video-carrier-secret"
DEFAULT_MESSAGE = "RootVPN video carrier says hello"


@dataclass(frozen=True)
class VideoMessageResult:
    ok: bool
    room_id: str
    server_url: str
    elapsed_ms: int
    publisher_identity: str
    receiver_identity: str
    message: str
    frame_width: int
    frame_height: int
    max_payload_bytes: int
    decode_attempts: int

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "room_id": self.room_id,
            "server_url": self.server_url,
            "elapsed_ms": self.elapsed_ms,
            "publisher_identity": self.publisher_identity,
            "receiver_identity": self.receiver_identity,
            "message": self.message,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "max_payload_bytes": self.max_payload_bytes,
            "decode_attempts": self.decode_attempts,
        }


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


async def wbstream_video_message_probe(
    room: str,
    *,
    message: str = DEFAULT_MESSAGE,
    secret: str = DEFAULT_SECRET,
    timeout_sec: float = 60.0,
) -> VideoMessageResult:
    room_id = extract_room_id(room)
    payload = message.encode("utf-8")
    payload_limit = max_payload_bytes()
    if len(payload) > payload_limit:
        raise ValueError(f"message is too large for one lab frame: {len(payload)} > {payload_limit}")

    started = time.perf_counter()
    details_a, details_b = await asyncio.gather(
        probe_room(room_id, display_name="RootVPN Frame A", timeout_sec=timeout_sec),
        probe_room(room_id, display_name="RootVPN Frame B", timeout_sec=timeout_sec),
    )
    if details_a.server_url != details_b.server_url:
        raise RuntimeError(
            f"WB Stream returned different LiveKit servers: {details_a.server_url} != {details_b.server_url}"
        )

    room_a = rtc.Room()
    room_b = rtc.Room()
    loop = asyncio.get_running_loop()
    decoded_message: asyncio.Future[str] = loop.create_future()
    stream_tasks: set[asyncio.Task[None]] = set()
    push_task: asyncio.Task[None] | None = None
    decode_attempts = 0

    frame_payload = encode_frame_rgba(payload, secret=secret)

    async def process_video_stream(stream: rtc.VideoStream) -> None:
        nonlocal decode_attempts
        async for event in stream:
            frame = event.frame.convert(rtc.VideoBufferType.RGBA)
            decode_attempts += 1
            try:
                chunk = decode_frame_rgba(
                    bytes(frame.data),
                    secret=secret,
                    width=frame.width,
                    height=frame.height,
                    cell_size=DEFAULT_CELL_SIZE,
                )
            except VideoFrameCodecError:
                continue
            text = chunk.payload.decode("utf-8", errors="replace")
            if not decoded_message.done():
                decoded_message.set_result(text)
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
        track = rtc.LocalVideoTrack.create_video_track("rootvpn-frame-carrier", source)
        opts = rtc.TrackPublishOptions()
        opts.source = rtc.TrackSource.SOURCE_CAMERA
        await room_a.local_participant.publish_track(track, opts)

        async def push_frames() -> None:
            while True:
                source.capture_frame(
                    rtc.VideoFrame(
                        DEFAULT_WIDTH,
                        DEFAULT_HEIGHT,
                        rtc.VideoBufferType.RGBA,
                        frame_payload,
                    )
                )
                await asyncio.sleep(1.0 / FPS)

        push_task = asyncio.create_task(push_frames())
        received = await asyncio.wait_for(decoded_message, timeout=timeout_sec)

        return VideoMessageResult(
            ok=True,
            room_id=room_id,
            server_url=details_a.server_url,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            publisher_identity=_participant_identity(room_a),
            receiver_identity=_participant_identity(room_b),
            message=received,
            frame_width=DEFAULT_WIDTH,
            frame_height=DEFAULT_HEIGHT,
            max_payload_bytes=payload_limit,
            decode_attempts=decode_attempts,
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
    parser = argparse.ArgumentParser(description="Send an encrypted one-frame payload through WB Stream video")
    parser.add_argument("room", help="WB Stream room ID or URL")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--secret", default=DEFAULT_SECRET)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    args = parser.parse_args()

    result = await wbstream_video_message_probe(
        args.room,
        message=args.message,
        secret=args.secret,
        timeout_sec=args.timeout_sec,
    )
    print(json.dumps(result.safe_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
