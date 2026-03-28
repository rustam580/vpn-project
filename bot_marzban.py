from __future__ import annotations

from typing import Any, Protocol

import httpx

class MarzbanClient:
    def __init__(self, settings: "SettingsLike"):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.marzban_base_url,
            timeout=20.0,
            verify=settings.marzban_verify_ssl,
        )
        self.token: str | None = None

    async def close(self) -> None:
        await self.client.aclose()

    async def auth(self) -> None:
        r = await self.client.post(
            "/api/admin/token",
            data={"username": self.settings.marzban_username, "password": self.settings.marzban_password},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Marzban auth failed: {r.status_code} {r.text}")
        token = r.json().get("access_token")
        if not token:
            raise RuntimeError("Marzban auth failed: access_token missing")
        self.token = token

    async def req(self, method: str, path: str, *, allow_404: bool = False, **kwargs: Any) -> Any:
        if not self.token:
            await self.auth()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.token}"
        r = await self.client.request(method, path, headers=headers, **kwargs)
        if r.status_code == 401:
            await self.auth()
            headers["Authorization"] = f"Bearer {self.token}"
            r = await self.client.request(method, path, headers=headers, **kwargs)
        if r.status_code == 404 and allow_404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"Marzban API error: {r.status_code} {r.text}")
        return r.json() if r.content else None

    async def get_user(self, username: str) -> dict[str, Any] | None:
        return await self.req("GET", f"/api/user/{username}", allow_404=True)

    async def get_inbound_tags(self, protocol: str) -> list[str]:
        data = await self.req("GET", "/api/inbounds")
        items = data.get(protocol, []) if isinstance(data, dict) else []
        tags = [item["tag"] for item in items if item.get("tag")]
        if not tags:
            raise RuntimeError(f"No inbounds for protocol '{protocol}'")
        return tags

    async def create_user(self, *, username: str, expire: int, data_limit: int) -> dict[str, Any]:
        protocol = self.settings.marzban_proxy_protocol
        payload = {
            "username": username,
            "status": "active",
            "expire": expire,
            "data_limit": data_limit,
            "data_limit_reset_strategy": "no_reset",
            "proxies": {protocol: {}},
            "inbounds": {protocol: await self.get_inbound_tags(protocol)},
        }
        return await self.req("POST", "/api/user", json=payload)

    async def modify_user(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.req("PUT", f"/api/user/{username}", json=payload)


class SettingsLike(Protocol):
    marzban_base_url: str
    marzban_verify_ssl: bool
    marzban_username: str
    marzban_password: str
    marzban_proxy_protocol: str

