from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout

API_BASE = "https://stream.wb.ru"
DEVICE_TYPE = "PARTICIPANT_DEVICE_TYPE_WEB_DESKTOP"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RootVPN-WebRTC-Lab/0.1"


@dataclass(frozen=True)
class WBConnectionDetails:
    room_id: str
    room_token: str
    server_url: str
    display_name: str

    def safe_dict(self, *, show_token: bool = False) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "server_url": self.server_url,
            "display_name": self.display_name,
            "room_token": self.room_token if show_token else mask_secret(self.room_token),
            "room_token_len": len(self.room_token),
        }


class WBStreamAPIError(RuntimeError):
    pass


def extract_room_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("room id/url is required")
    if raw.startswith("http://") or raw.startswith("https://"):
        path = urlparse(raw).path.strip("/")
        if not path:
            raise ValueError(f"cannot extract room id from URL: {raw}")
        raw = path.rsplit("/", 1)[-1]
    if not raw:
        raise ValueError("room id is empty")
    return raw


def mask_secret(value: str, *, visible: int = 8) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


async def _expect_json(response, *, label: str) -> dict[str, Any]:
    text = await response.text()
    if response.status < 200 or response.status >= 300:
        raise WBStreamAPIError(f"{label} failed: HTTP {response.status}: {text[:1000]}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WBStreamAPIError(f"{label} returned non-json: {text[:1000]}") from exc
    return payload


async def register_guest(
    session: ClientSession,
    *,
    display_name: str,
    device_name: str = "Windows",
) -> str:
    payload = {
        "displayName": display_name,
        "device": {
            "deviceName": device_name,
            "deviceType": DEVICE_TYPE,
        },
    }
    async with session.post(
        f"{API_BASE}/auth/api/v1/auth/user/guest-register",
        json=payload,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    ) as response:
        body = await _expect_json(response, label="guest-register")
    token = str(body.get("accessToken") or "").strip()
    if not token:
        raise WBStreamAPIError(f"guest-register response has no accessToken: {body}")
    return token


async def join_room(session: ClientSession, *, access_token: str, room_id: str) -> None:
    async with session.post(
        f"{API_BASE}/api-room/api/v1/room/{room_id}/join",
        json={},
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    ) as response:
        await _expect_json(response, label="join-room")


async def create_room(session: ClientSession, *, access_token: str) -> str:
    payload = {
        "roomType": "ROOM_TYPE_ALL_ON_SCREEN",
        "roomPrivacy": "ROOM_PRIVACY_FREE",
    }
    async with session.post(
        f"{API_BASE}/api-room/api/v2/room",
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    ) as response:
        body = await _expect_json(response, label="create-room")
    room_id = str(body.get("roomId") or "").strip()
    if not room_id:
        raise WBStreamAPIError(f"create-room response has no roomId: {body}")
    return room_id


async def get_connection_details(
    session: ClientSession,
    *,
    access_token: str,
    room_id: str,
    display_name: str,
) -> WBConnectionDetails:
    async with session.get(
        f"{API_BASE}/api-room-manager/v2/room/{room_id}/connection-details",
        params={"deviceType": DEVICE_TYPE, "displayName": display_name},
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    ) as response:
        body = await _expect_json(response, label="connection-details")
    room_token = str(body.get("roomToken") or "").strip()
    server_url = str(body.get("serverUrl") or "").strip()
    if not room_token:
        raise WBStreamAPIError(f"connection-details response has no roomToken: {body}")
    return WBConnectionDetails(
        room_id=room_id,
        room_token=room_token,
        server_url=server_url,
        display_name=display_name,
    )


async def probe_room(
    room: str,
    *,
    display_name: str = "RootVPN Lab",
    timeout_sec: float = 20.0,
) -> WBConnectionDetails:
    room_id = extract_room_id(room)
    timeout = ClientTimeout(total=timeout_sec)
    async with ClientSession(timeout=timeout) as session:
        access_token = await register_guest(session, display_name=display_name)
        await join_room(session, access_token=access_token, room_id=room_id)
        return await get_connection_details(
            session,
            access_token=access_token,
            room_id=room_id,
            display_name=display_name,
        )


async def probe_new_room(
    *,
    display_name: str = "RootVPN Lab",
    timeout_sec: float = 20.0,
) -> WBConnectionDetails:
    timeout = ClientTimeout(total=timeout_sec)
    async with ClientSession(timeout=timeout) as session:
        access_token = await register_guest(session, display_name=display_name)
        room_id = await create_room(session, access_token=access_token)
        return await get_connection_details(
            session,
            access_token=access_token,
            room_id=room_id,
            display_name=display_name,
        )


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Probe WB Stream room access for the WebRTC lab")
    parser.add_argument("room", nargs="?", help="WB Stream room ID or URL")
    parser.add_argument("--create-room", action="store_true", help="create a new WB Stream guest room first")
    parser.add_argument("--display-name", default="RootVPN Lab")
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    parser.add_argument("--show-token", action="store_true", help="print the full room token")
    args = parser.parse_args()

    if args.create_room:
        details = await probe_new_room(display_name=args.display_name, timeout_sec=args.timeout_sec)
    else:
        if not args.room:
            parser.error("room is required unless --create-room is used")
        details = await probe_room(
            args.room,
            display_name=args.display_name,
            timeout_sec=args.timeout_sec,
        )
    print(json.dumps(details.safe_dict(show_token=args.show_token), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
