from __future__ import annotations

from collections.abc import Iterable
from typing import Any


SENSITIVE_KEYS = {
    "privateKey",
    "private_key",
    "password",
    "secret",
    "token",
}


def _safe_value(value: Any, *, limit: int = 80) -> str:
    text = str(value)
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _walk_values(data: Any, key_names: set[str]) -> Iterable[tuple[str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in SENSITIVE_KEYS:
                continue
            if key in key_names:
                yield key, value
            yield from _walk_values(value, key_names)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_values(item, key_names)


def _first_value(data: Any, *key_names: str) -> str:
    wanted = set(key_names)
    for _, value in _walk_values(data, wanted):
        if value not in (None, "", [], {}):
            return _safe_value(value)
    return "-"


def _unique_values(data: Any, *key_names: str, limit: int = 8) -> list[str]:
    wanted = set(key_names)
    values: list[str] = []
    seen: set[str] = set()
    for key, value in _walk_values(data, wanted):
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = [value]
        for item in raw_items:
            if isinstance(item, dict):
                continue
            rendered = _safe_value(item)
            if rendered in seen:
                continue
            seen.add(rendered)
            values.append(rendered)
            if len(values) >= limit:
                return values
    return values


def _protocol_items(inbounds: Any, protocol: str) -> list[dict[str, Any]]:
    if isinstance(inbounds, dict):
        raw_items = inbounds.get(protocol, [])
    elif isinstance(inbounds, list):
        raw_items = inbounds
    else:
        raw_items = []
    return [item for item in raw_items if isinstance(item, dict)]


def format_marzban_inbounds_report(
    inbounds: Any,
    *,
    protocol: str,
    config_delivery_mode: str,
    subscription_public_base_url: str,
) -> str:
    protocol = (protocol or "vless").strip().lower()
    items = _protocol_items(inbounds, protocol)
    available_protocols = (
        ", ".join(sorted(str(key) for key in inbounds.keys()))
        if isinstance(inbounds, dict) and inbounds
        else "-"
    )
    sub_base = "set" if (subscription_public_base_url or "").strip() else "empty"

    lines = [
        "🧭 Marzban inbound diagnostics",
        f"Bot protocol: {protocol}",
        f"Delivery mode: {config_delivery_mode}",
        f"Subscription public base: {sub_base}",
        f"API protocols: {available_protocols}",
        f"{protocol} inbounds visible to bot: {len(items)}",
    ]

    if not items:
        lines.append("")
        lines.append("Problem: bot does not see usable inbounds for this protocol.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Inbounds assigned to new users:")
    for index, item in enumerate(items, start=1):
        tag = item.get("tag") or item.get("remark") or item.get("name") or "-"
        network = _first_value(item, "network")
        security = _first_value(item, "security")
        port = _first_value(item, "port")
        flows = _unique_values(item, "flow")
        server_names = _unique_values(item, "serverNames", "serverName", "sni", limit=4)
        fingerprints = _unique_values(item, "fingerprint", "fp", limit=4)

        lines.append(f"{index}. tag: {tag}")
        lines.append(f"   port: {port}; network: {network}; security: {security}")
        lines.append(f"   flow fields in API: {', '.join(flows) if flows else '-'}")
        lines.append(f"   server names/SNI: {', '.join(server_names) if server_names else '-'}")
        lines.append(f"   fingerprints: {', '.join(fingerprints) if fingerprints else '-'}")

    lines.append("")
    lines.append("If Xray logs say 'account ... is not able to use the flow xtls-rprx-vision',")
    lines.append("the imported client profile contains Vision flow but the Marzban user/inbound does not allow it.")
    lines.append("Do not replace working user subscription URLs with hand-built raw links while checking this.")
    return "\n".join(lines)
