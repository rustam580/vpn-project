from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from livekit import rtc

from wbstream_api import extract_room_id, probe_room

WIDTH = 160
HEIGHT = 120
FPS = 8


@dataclass(frozen=True)
class VideoProbeResult:
    ok: bool
    room_id: str
    server_url: str
    elapsed_ms: int
    publisher_identity: str
    receiver_identity: str
    frame_width: int
    frame_height: int
    frame_bytes: int

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "room_id": self.room_id,
            "server_url": self.server_url,
            "elapsed_ms": self.elapsed_ms,
            "publisher_identity": self.publisher_identity,
            "receiver_identity": self.receiver_identity,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "frame_bytes": self.frame_bytes,
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


def _rgba_frame(counter: int) -> bytes:
    data = bytearray(WIDTH * HEIGHT * 4)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            offset = (y * WIDTH + x) * 4
            data[offset] = (x + counter * 7) % 256
            data[offset + 1] = (y + counter * 11) % 256
            data[offset + 2] = (x + y + counter * 13) % 256
            data[offset + 3] = 255
    return bytes(data)


async def wbstream_video_probe(room: str, *, timeout_sec: float = 45.0) -> VideoProbeResult:
    room_id = extract_room_id(room)
    started = time.perf_counter()
    details_a, details_b = await asyncio.gather(
        probe_room(room_id, display_name="RootVPN Video A", timeout_sec=timeout_sec),
        probe_room(room_id, display_name="RootVPN Video B", timeout_sec=timeout_sec),
    )
    if details_a.server_url != details_b.server_url:
        raise RuntimeError(
            f"WB Stream returned different LiveKit servers: {details_a.server_url} != {details_b.server_url}"
        )

    room_a = rtc.Room()
    room_b = rtc.Room()
    loop = asyncio.get_running_loop()
    first_frame: asyncio.Future[tuple[int, int, int]] = loop.create_future()
    stream_tasks: set[asyncio.Task[None]] = set()
    push_task: asyncio.Task[None] | None = None

    async def process_video_stream(stream: rtc.VideoStream) -> None:
        async for event in stream:
            if not first_frame.done():
                frame = event.frame.convert(rtc.VideoBufferType.RGBA)
                first_frame.set_result((frame.width, frame.height, len(frame.data)))
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

        source = rtc.VideoSource(width=WIDTH, height=HEIGHT)
        track = rtc.LocalVideoTrack.create_video_track("rootvpn-video-probe", source)
        opts = rtc.TrackPublishOptions()
        opts.source = rtc.TrackSource.SOURCE_CAMERA
        await room_a.local_participant.publish_track(track, opts)

        async def push_frames() -> None:
            counter = 0
            while True:
                source.capture_frame(
                    rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, _rgba_frame(counter))
                )
                counter += 1
                await asyncio.sleep(1.0 / FPS)

        push_task = asyncio.create_task(push_frames())
        width, height, frame_bytes = await asyncio.wait_for(first_frame, timeout=timeout_sec)

        return VideoProbeResult(
            ok=True,
            room_id=room_id,
            server_url=details_a.server_url,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            publisher_identity=_participant_identity(room_a),
            receiver_identity=_participant_identity(room_b),
            frame_width=width,
            frame_height=height,
            frame_bytes=frame_bytes,
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
    parser = argparse.ArgumentParser(description="Publish and receive a synthetic video track through WB Stream")
    parser.add_argument("room", help="WB Stream room ID or URL")
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    args = parser.parse_args()

    result = await wbstream_video_probe(args.room, timeout_sec=args.timeout_sec)
    print(json.dumps(result.safe_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
