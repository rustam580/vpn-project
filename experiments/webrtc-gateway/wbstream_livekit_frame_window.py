from __future__ import annotations

import argparse
import asyncio
import hashlib
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
    decode_frame_rgba,
    encode_frame_rgba,
    encode_payload_frames_rgba,
    make_ack_payload,
    max_payload_bytes,
    parse_ack_payload,
)
from video_window import SlidingWindowSender
from wbstream_api import extract_room_id, probe_room

FPS = 8
ACK_FPS = 4
DEFAULT_WINDOW_SIZE = 4
DEFAULT_RETRY_TIMEOUT_SEC = 2.5
DEFAULT_SECRET = "rootvpn-lab-video-carrier-secret"
DEFAULT_MESSAGE = "RootVPN windowed video carrier " * 40


@dataclass(frozen=True)
class VideoWindowResult:
    ok: bool
    room_id: str
    server_url: str
    elapsed_ms: int
    sender_identity: str
    receiver_identity: str
    payload_bytes: int
    received_sha256: str
    frame_width: int
    frame_height: int
    max_payload_per_frame: int
    encoded_frames: int
    chunks_received: int
    acked_chunks: int
    data_decode_attempts: int
    ack_decode_attempts: int
    data_frames_sent: int
    retransmits: int
    ack_frames_sent: int
    fps: int
    ack_fps: int
    window_size: int
    retry_timeout_sec: float
    throughput_bps: float

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "room_id": self.room_id,
            "server_url": self.server_url,
            "elapsed_ms": self.elapsed_ms,
            "sender_identity": self.sender_identity,
            "receiver_identity": self.receiver_identity,
            "payload_bytes": self.payload_bytes,
            "received_sha256": self.received_sha256,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "max_payload_per_frame": self.max_payload_per_frame,
            "encoded_frames": self.encoded_frames,
            "chunks_received": self.chunks_received,
            "acked_chunks": self.acked_chunks,
            "data_decode_attempts": self.data_decode_attempts,
            "ack_decode_attempts": self.ack_decode_attempts,
            "data_frames_sent": self.data_frames_sent,
            "retransmits": self.retransmits,
            "ack_frames_sent": self.ack_frames_sent,
            "fps": self.fps,
            "ack_fps": self.ack_fps,
            "window_size": self.window_size,
            "retry_timeout_sec": self.retry_timeout_sec,
            "throughput_bps": round(self.throughput_bps, 2),
        }


def _sha256_hex(payload: bytes) -> str:
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


async def wbstream_video_window_probe(
    room: str,
    *,
    message: str = DEFAULT_MESSAGE,
    payload_bytes: int | None = None,
    secret: str = DEFAULT_SECRET,
    timeout_sec: float = 120.0,
    fps: int = FPS,
    ack_fps: int = ACK_FPS,
    window_size: int = DEFAULT_WINDOW_SIZE,
    retry_timeout_sec: float = DEFAULT_RETRY_TIMEOUT_SEC,
) -> VideoWindowResult:
    room_id = extract_room_id(room)
    payload = _build_payload(message, payload_bytes)
    data_frames = encode_payload_frames_rgba(payload, secret=secret)
    total_chunks = len(data_frames)
    sender = SlidingWindowSender(
        total_chunks=total_chunks,
        window_size=window_size,
        retry_timeout_sec=retry_timeout_sec,
    )
    started = time.perf_counter()

    details_a, details_b = await asyncio.gather(
        probe_room(room_id, display_name="RootVPN Window A", timeout_sec=timeout_sec),
        probe_room(room_id, display_name="RootVPN Window B", timeout_sec=timeout_sec),
    )
    if details_a.server_url != details_b.server_url:
        raise RuntimeError(
            f"WB Stream returned different LiveKit servers: {details_a.server_url} != {details_b.server_url}"
        )

    room_a = rtc.Room()
    room_b = rtc.Room()
    loop = asyncio.get_running_loop()
    received_payload: asyncio.Future[bytes] = loop.create_future()
    all_acked: asyncio.Future[None] = loop.create_future()
    reassembler = VideoFrameReassembler(secret=secret)
    received_seqs: set[int] = set()
    stream_tasks: set[asyncio.Task[None]] = set()
    push_tasks: set[asyncio.Task[None]] = set()
    data_decode_attempts = 0
    ack_decode_attempts = 0
    ack_frames_sent = 0

    async def process_data_stream(stream: rtc.VideoStream) -> None:
        nonlocal data_decode_attempts
        async for event in stream:
            frame = event.frame.convert(rtc.VideoBufferType.RGBA)
            data_decode_attempts += 1
            try:
                chunk = decode_frame_rgba(
                    bytes(frame.data),
                    secret=secret,
                    width=frame.width,
                    height=frame.height,
                    cell_size=DEFAULT_CELL_SIZE,
                )
                maybe_payload = reassembler.add_chunk(chunk)
            except VideoFrameCodecError:
                continue
            received_seqs.add(chunk.seq)
            if maybe_payload is not None and not received_payload.done():
                received_payload.set_result(maybe_payload)

    async def process_ack_stream(stream: rtc.VideoStream) -> None:
        nonlocal ack_decode_attempts
        async for event in stream:
            frame = event.frame.convert(rtc.VideoBufferType.RGBA)
            ack_decode_attempts += 1
            try:
                chunk = decode_frame_rgba(
                    bytes(frame.data),
                    secret=secret,
                    width=frame.width,
                    height=frame.height,
                    cell_size=DEFAULT_CELL_SIZE,
                )
                ack = parse_ack_payload(chunk.payload)
            except VideoFrameCodecError:
                continue
            if ack.total != total_chunks:
                continue
            sender.update_ack(ack.received)
            if sender.complete and not all_acked.done():
                all_acked.set_result(None)
                break

    try:
        @room_b.on("track_subscribed")
        def on_b_track_subscribed(
            track: rtc.Track,
            _publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            if participant.identity != _participant_identity(room_a):
                return
            task = asyncio.create_task(process_data_stream(rtc.VideoStream(track)))
            stream_tasks.add(task)
            task.add_done_callback(stream_tasks.discard)

        @room_a.on("track_subscribed")
        def on_a_track_subscribed(
            track: rtc.Track,
            _publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            if participant.identity != _participant_identity(room_b):
                return
            task = asyncio.create_task(process_ack_stream(rtc.VideoStream(track)))
            stream_tasks.add(task)
            task.add_done_callback(stream_tasks.discard)

        await asyncio.gather(
            room_a.connect(details_a.server_url, details_a.room_token),
            room_b.connect(details_b.server_url, details_b.room_token),
        )
        await _wait_for_remote_identity(room_a, timeout_sec=timeout_sec)
        await _wait_for_remote_identity(room_b, timeout_sec=timeout_sec)

        data_source = rtc.VideoSource(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT)
        data_track = rtc.LocalVideoTrack.create_video_track("rootvpn-window-data", data_source)
        ack_source = rtc.VideoSource(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT)
        ack_track = rtc.LocalVideoTrack.create_video_track("rootvpn-window-ack", ack_source)
        opts = rtc.TrackPublishOptions()
        opts.source = rtc.TrackSource.SOURCE_CAMERA
        await room_a.local_participant.publish_track(data_track, opts)
        await room_b.local_participant.publish_track(ack_track, opts)

        async def push_data_frames() -> None:
            await asyncio.sleep(1.0)
            while not all_acked.done():
                now = time.perf_counter()
                due = sender.due(now)
                if not due:
                    await asyncio.sleep(min(1.0 / fps, retry_timeout_sec / 4))
                    continue
                for seq in due:
                    if all_acked.done():
                        break
                    data_source.capture_frame(
                        rtc.VideoFrame(DEFAULT_WIDTH, DEFAULT_HEIGHT, rtc.VideoBufferType.RGBA, data_frames[seq])
                    )
                    sender.mark_sent(seq, time.perf_counter())
                    await asyncio.sleep(1.0 / fps)

        async def push_ack_frames() -> None:
            nonlocal ack_frames_sent
            await asyncio.sleep(1.0)
            while not all_acked.done():
                ack_payload = make_ack_payload(total_chunks, received_seqs)
                ack_frame = encode_frame_rgba(ack_payload, secret=secret)
                ack_source.capture_frame(
                    rtc.VideoFrame(DEFAULT_WIDTH, DEFAULT_HEIGHT, rtc.VideoBufferType.RGBA, ack_frame)
                )
                ack_frames_sent += 1
                await asyncio.sleep(1.0 / ack_fps)

        for task in (asyncio.create_task(push_data_frames()), asyncio.create_task(push_ack_frames())):
            push_tasks.add(task)
            task.add_done_callback(push_tasks.discard)

        received, _ = await asyncio.wait_for(
            asyncio.gather(received_payload, all_acked),
            timeout=timeout_sec,
        )
        if received != payload:
            raise RuntimeError("received payload hash mismatch")

        elapsed = time.perf_counter() - started
        stats = sender.stats
        return VideoWindowResult(
            ok=True,
            room_id=room_id,
            server_url=details_a.server_url,
            elapsed_ms=int(elapsed * 1000),
            sender_identity=_participant_identity(room_a),
            receiver_identity=_participant_identity(room_b),
            payload_bytes=len(payload),
            received_sha256=_sha256_hex(received),
            frame_width=DEFAULT_WIDTH,
            frame_height=DEFAULT_HEIGHT,
            max_payload_per_frame=max_payload_bytes(),
            encoded_frames=total_chunks,
            chunks_received=reassembler.chunks_received,
            acked_chunks=stats.acked_chunks,
            data_decode_attempts=data_decode_attempts,
            ack_decode_attempts=ack_decode_attempts,
            data_frames_sent=stats.frames_sent,
            retransmits=stats.retransmits,
            ack_frames_sent=ack_frames_sent,
            fps=fps,
            ack_fps=ack_fps,
            window_size=stats.window_size,
            retry_timeout_sec=stats.retry_timeout_sec,
            throughput_bps=len(payload) / elapsed if elapsed > 0 else 0.0,
        )
    finally:
        for task in list(push_tasks):
            task.cancel()
        for task in list(stream_tasks):
            task.cancel()
        if room_b.isconnected():
            await room_b.disconnect()
        if room_a.isconnected():
            await room_a.disconnect()


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Send WB Stream video payload with sliding-window ACKs")
    parser.add_argument("room", help="WB Stream room ID or URL")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--payload-bytes", type=int)
    parser.add_argument("--secret", default=DEFAULT_SECRET)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--ack-fps", type=int, default=ACK_FPS)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--retry-timeout-sec", type=float, default=DEFAULT_RETRY_TIMEOUT_SEC)
    args = parser.parse_args()

    result = await wbstream_video_window_probe(
        args.room,
        message=args.message,
        payload_bytes=args.payload_bytes,
        secret=args.secret,
        timeout_sec=args.timeout_sec,
        fps=args.fps,
        ack_fps=args.ack_fps,
        window_size=args.window_size,
        retry_timeout_sec=args.retry_timeout_sec,
    )
    print(json.dumps(result.safe_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
