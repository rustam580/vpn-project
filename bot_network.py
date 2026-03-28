from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

def _read_iface_bytes(iface: str) -> tuple[int, int] | None:
    try:
        data = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    needle = f"{iface}:"
    for line in data:
        line = line.strip()
        if not line.startswith(needle):
            continue
        payload = line.split(":", 1)[1].split()
        if len(payload) < 16:
            return None
        rx = int(payload[0])
        tx = int(payload[8])
        return rx, tx
    return None


async def measure_iface_mbps(iface: str, duration: int = 5) -> float | None:
    start = _read_iface_bytes(iface)
    if not start:
        return None
    await asyncio.sleep(max(1, duration))
    end = _read_iface_bytes(iface)
    if not end:
        return None
    delta_bytes = (end[0] + end[1]) - (start[0] + start[1])
    if delta_bytes < 0:
        return None
    return (delta_bytes * 8) / (duration * 1024 * 1024)


def _parse_sar_dev_output(raw_text: str, target_iface: str) -> float | None:
    rx = 0.0
    tx = 0.0
    count = 0
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if (
            low.startswith("linux")
            or "iface" in low
            or low.startswith("average:")
            or low.startswith("среднее:")
            or low.startswith("средн.:")
        ):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            iface_idx = parts.index(target_iface)
        except ValueError:
            continue
        # Expected columns after IFACE:
        # rxpck/s txpck/s rxkB/s txkB/s ...
        if len(parts) <= iface_idx + 4:
            continue
        try:
            rx_kbps = float(parts[iface_idx + 3])
            tx_kbps = float(parts[iface_idx + 4])
        except ValueError:
            continue
        rx += rx_kbps
        tx += tx_kbps
        count += 1
    if count == 0:
        return None
    avg_kbps = (rx + tx) / count
    return (avg_kbps * 8) / 1024


async def measure_iface_mbps_sar(iface: str, duration: int = 60) -> float | None:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    try:
        proc = await asyncio.create_subprocess_exec(
            "sar",
            "-n",
            "DEV",
            "1",
            str(duration),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=duration + 5)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if not stdout:
        return None
    text = stdout.decode("utf-8", errors="ignore")
    return _parse_sar_dev_output(text, iface)

