from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from livekit import rtc

from wbstream_api import WBConnectionDetails, extract_room_id, probe_new_room, probe_room

DEFAULT_TOPIC = "rootvpn-lab"


@dataclass(frozen=True)
class PingResult:
    room_id: str
    server_url: str
    topic: str
    elapsed_ms: int
    participant_a: str
    participant_b: str
    received_by_b: str
    received_by_a: str

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "room_id": self.room_id,
            "server_url": self.server_url,
            "topic": self.topic,
            "elapsed_ms": self.elapsed_ms,
            "participant_a": self.participant_a,
            "participant_b": self.participant_b,
            "received_by_b": self.received_by_b,
            "received_by_a": self.received_by_a,
        }


async def _connect_room(details: WBConnectionDetails) -> rtc.Room:
    room = rtc.Room()
    await room.connect(details.server_url, details.room_token)
    return room


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


async def livekit_ping_pong(
    room: str,
    *,
    create_room: bool = False,
    topic: str = DEFAULT_TOPIC,
    timeout_sec: float = 30.0,
) -> PingResult:
    started = time.perf_counter()

    if create_room:
        details_a = await probe_new_room(display_name="RootVPN Lab A", timeout_sec=timeout_sec)
        room_id = details_a.room_id
        details_b = await probe_room(room_id, display_name="RootVPN Lab B", timeout_sec=timeout_sec)
    else:
        room_id = extract_room_id(room)
        details_a, details_b = await asyncio.gather(
            probe_room(room_id, display_name="RootVPN Lab A", timeout_sec=timeout_sec),
            probe_room(room_id, display_name="RootVPN Lab B", timeout_sec=timeout_sec),
        )
    if details_a.server_url != details_b.server_url:
        raise RuntimeError(
            f"WB Stream returned different LiveKit servers: {details_a.server_url} != {details_b.server_url}"
        )

    room_a = rtc.Room()
    room_b = rtc.Room()
    loop = asyncio.get_running_loop()
    b_received: asyncio.Future[str] = loop.create_future()
    a_received: asyncio.Future[str] = loop.create_future()
    background_tasks: set[asyncio.Task[None]] = set()

    try:
        @room_a.on("data_received")
        def on_a_data(packet: rtc.DataPacket) -> None:
            if packet.topic != topic:
                return
            text = packet.data.decode("utf-8", errors="replace")
            if text == "pong" and not a_received.done():
                a_received.set_result(text)

        @room_b.on("data_received")
        def on_b_data(packet: rtc.DataPacket) -> None:
            if packet.topic != topic:
                return
            text = packet.data.decode("utf-8", errors="replace")
            if not b_received.done():
                b_received.set_result(text)
            if text == "ping":
                task = asyncio.create_task(
                    room_b.local_participant.publish_data(
                        "pong",
                        reliable=True,
                        destination_identities=[_participant_identity(room_a)],
                        topic=topic,
                    )
                )
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)

        await asyncio.gather(
            room_a.connect(details_a.server_url, details_a.room_token),
            room_b.connect(details_b.server_url, details_b.room_token),
        )
        remote_b_identity = await _wait_for_remote_identity(room_a, timeout_sec=timeout_sec)
        await _wait_for_remote_identity(room_b, timeout_sec=timeout_sec)

        await room_a.local_participant.publish_data(
            "ping",
            reliable=True,
            destination_identities=[remote_b_identity],
            topic=topic,
        )

        received_by_b, received_by_a = await asyncio.wait_for(
            asyncio.gather(b_received, a_received),
            timeout=timeout_sec,
        )

        return PingResult(
            room_id=room_id,
            server_url=details_a.server_url,
            topic=topic,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            participant_a=_participant_identity(room_a),
            participant_b=_participant_identity(room_b),
            received_by_b=received_by_b,
            received_by_a=received_by_a,
        )
    finally:
        if room_b.isconnected():
            await room_b.disconnect()
        if room_a.isconnected():
            await room_a.disconnect()


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Send a LiveKit data-channel ping through a WB Stream room")
    parser.add_argument("room", nargs="?", help="WB Stream room ID or URL")
    parser.add_argument("--create-room", action="store_true", help="create a new WB Stream guest room first")
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    args = parser.parse_args()

    if not args.create_room and not args.room:
        parser.error("room is required unless --create-room is used")
    result = await livekit_ping_pong(
        args.room or "",
        create_room=args.create_room,
        topic=args.topic,
        timeout_sec=args.timeout_sec,
    )
    print(json.dumps(result.safe_dict(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
