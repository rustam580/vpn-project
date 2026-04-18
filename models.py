from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Plan:
    key: str
    title: str
    days: int
    gb: int
    rub: float


@dataclass(frozen=True)
class MarzbanUser:
    username: str
    status: str
    expire: int
    used_traffic: int
    data_limit: int
    links: list[str]
    subscription_url: str
    subscription_link: str
    subscription: str
    sub_link: str
    subscription_links: list[str] | dict[str, str] | None

    @staticmethod
    def from_payload(payload: Mapping[str, Any] | "MarzbanUser" | None) -> "MarzbanUser":
        if isinstance(payload, MarzbanUser):
            return payload
        raw = dict(payload or {})
        raw_links = raw.get("links")
        links: list[str] = []
        if isinstance(raw_links, list):
            for item in raw_links:
                if isinstance(item, str):
                    trimmed = item.strip()
                    if trimmed:
                        links.append(trimmed)

        extra_links = raw.get("subscription_links")
        if isinstance(extra_links, list):
            normalized_extra: list[str] | dict[str, str] | None = [
                str(x).strip() for x in extra_links if isinstance(x, str) and str(x).strip()
            ]
        elif isinstance(extra_links, dict):
            normalized_extra = {
                str(k): str(v).strip()
                for k, v in extra_links.items()
                if isinstance(v, str) and str(v).strip()
            }
        else:
            normalized_extra = None

        return MarzbanUser(
            username=str(raw.get("username") or ""),
            status=str(raw.get("status") or "unknown"),
            expire=int(raw.get("expire") or 0),
            used_traffic=int(raw.get("used_traffic") or 0),
            data_limit=int(raw.get("data_limit") or 0),
            links=links,
            subscription_url=str(raw.get("subscription_url") or "").strip(),
            subscription_link=str(raw.get("subscription_link") or "").strip(),
            subscription=str(raw.get("subscription") or "").strip(),
            sub_link=str(raw.get("sub_link") or "").strip(),
            subscription_links=normalized_extra,
        )
