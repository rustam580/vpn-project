from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout

WB_API_BASE = "https://stream.wb.ru"
WB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RootVPN-Rescue-RoomBroker/0.1"
DEFAULT_TOKEN_ENV = "WBSTREAM_ACCESS_TOKEN"
DEFAULT_TOKEN_FILE = "/etc/rootvpn/wbstream-access-token"


class RoomBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedRoom:
    room_id: str
    room_url: str


def build_create_room_payload() -> dict[str, str]:
    return {
        "roomType": "ROOM_TYPE_ALL_ON_SCREEN",
        "roomPrivacy": "ROOM_PRIVACY_FREE",
    }


def build_room_url(room_id: str, *, base_url: str = WB_API_BASE) -> str:
    return f"{base_url.rstrip('/')}/room/{room_id.strip()}"


def load_access_token(
    *,
    token: str = "",
    token_file: str = DEFAULT_TOKEN_FILE,
    token_env: str = DEFAULT_TOKEN_ENV,
) -> str:
    token = token.strip()
    if token:
        return token

    env_token = os.getenv(token_env, "").strip()
    if env_token:
        return env_token

    path = Path(token_file)
    if path.exists():
        file_token = path.read_text(encoding="utf-8").strip()
        if file_token:
            return file_token

    raise RoomBrokerError(
        f"WB access token is missing. Set {token_env} or write token to {token_file}."
    )


async def create_wbstream_room_with_token(
    *,
    access_token: str,
    base_url: str = WB_API_BASE,
    timeout_sec: float = 20.0,
    user_agent: str = WB_USER_AGENT,
) -> CreatedRoom:
    timeout = ClientTimeout(total=timeout_sec)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": user_agent,
    }
    async with ClientSession(timeout=timeout, headers=headers) as session:
        async with session.post(
            f"{base_url.rstrip('/')}/api-room/api/v2/room",
            json=build_create_room_payload(),
        ) as response:
            body = await _expect_json(response, label="create-room")

    room_id = str(body.get("roomId") or "").strip()
    if not room_id:
        raise RoomBrokerError(f"create-room response has no roomId: {body}")
    return CreatedRoom(room_id=room_id, room_url=build_room_url(room_id, base_url=base_url))


def format_broker_output(rooms: list[CreatedRoom]) -> str:
    return json.dumps(
        {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "rooms": [
                {
                    "room_id": room.room_id,
                    "room_url": room.room_url,
                }
                for room in rooms
            ],
        },
        ensure_ascii=False,
    )


async def _expect_json(response, *, label: str) -> dict[str, Any]:
    text = await response.text()
    if response.status < 200 or response.status >= 300:
        raise RoomBrokerError(f"{label} failed: HTTP {response.status}: {text[:1000]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RoomBrokerError(f"{label} returned non-json: {text[:1000]}") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create WB Stream rooms for RootVPN Rescue pool")
    parser.add_argument("--count", type=int, default=1, help="number of rooms to create")
    parser.add_argument("--token", default="", help="WB access token; prefer env/file in production")
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--base-url", default=WB_API_BASE)
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    return parser.parse_args()


async def _amain() -> int:
    args = _parse_args()
    count = max(1, min(10, int(args.count)))
    token = load_access_token(token=args.token, token_file=args.token_file, token_env=args.token_env)
    rooms: list[CreatedRoom] = []
    for _ in range(count):
        rooms.append(
            await create_wbstream_room_with_token(
                access_token=token,
                base_url=args.base_url,
                timeout_sec=args.timeout_sec,
            )
        )
    print(format_broker_output(rooms))
    return 0


def main() -> int:
    try:
        return asyncio.run(_amain())
    except RoomBrokerError as exc:
        print(f"room broker failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
