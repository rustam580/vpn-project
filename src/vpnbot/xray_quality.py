from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_TS_RE = re.compile(r"(?P<date>\d{4}[-/]\d{2}[-/]\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})")
_LEVEL_RE = re.compile(r"\[(?P<level>Debug|Info|Warning|Error)\]", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\b(?::\d{2,5})?")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_PORT_RE = re.compile(r":\d{2,5}\b")
_ID_RE = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class XrayErrorSummary:
    log_path: str
    window_minutes: int
    since_ts: int
    total: int
    top_levels: list[tuple[str, int]]
    top_signatures: list[tuple[str, int]]
    top_remote_ips: list[tuple[str, int]]
    samples: list[str]
    file_missing: bool = False
    read_error: str | None = None

    def has_problem(self, *, threshold: int) -> bool:
        return self.total >= max(1, int(threshold))


def _parse_ts(line: str) -> int | None:
    match = _TS_RE.search(line)
    if not match:
        return None
    raw = f"{match.group('date').replace('/', '-')} {match.group('time')}"
    try:
        return int(time.mktime(time.strptime(raw, "%Y-%m-%d %H:%M:%S")))
    except (OverflowError, ValueError):
        return None


def _level(line: str) -> str:
    match = _LEVEL_RE.search(line)
    if not match:
        return "unknown"
    return match.group("level").lower()


def _remote_ip(line: str) -> str | None:
    for match in _IP_RE.finditer(line):
        ip = match.group("ip")
        parts = ip.split(".")
        try:
            if all(0 <= int(part) <= 255 for part in parts):
                return ip
        except ValueError:
            continue
    return None


def _signature(line: str) -> str:
    text = _TS_RE.sub("", line, count=1)
    text = _LEVEL_RE.sub("[level]", text)
    text = _ID_RE.sub("[id]", text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _HEX_RE.sub("<hex>", text)
    text = _IP_RE.sub("<ip>", text)
    text = _PORT_RE.sub(":<port>", text)
    text = re.sub(r"\s+", " ", text).strip(" -:>")
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return text or "unknown"


def _tail_text(path: Path, *, max_bytes: int) -> str:
    with path.open("rb") as fh:
        try:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max(1, int(max_bytes))))
        except OSError:
            fh.seek(0)
        return fh.read().decode("utf-8", errors="replace")


def summarize_xray_error_lines(
    lines: Iterable[str],
    *,
    log_path: str,
    window_minutes: int,
    now_ts: int | None = None,
) -> XrayErrorSummary:
    now = int(now_ts if now_ts is not None else time.time())
    window = max(1, int(window_minutes))
    since_ts = now - window * 60

    level_counts: Counter[str] = Counter()
    signature_counts: Counter[str] = Counter()
    ip_counts: Counter[str] = Counter()
    samples: list[str] = []
    total = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        ts = _parse_ts(line)
        if ts is not None and ts < since_ts:
            continue
        total += 1
        level_counts[_level(line)] += 1
        signature_counts[_signature(line)] += 1
        ip = _remote_ip(line)
        if ip:
            ip_counts[ip] += 1
        if len(samples) < 5:
            samples.append(line[:260])

    return XrayErrorSummary(
        log_path=log_path,
        window_minutes=window,
        since_ts=since_ts,
        total=total,
        top_levels=level_counts.most_common(8),
        top_signatures=signature_counts.most_common(8),
        top_remote_ips=ip_counts.most_common(8),
        samples=samples,
    )


def summarize_xray_error_log(
    log_path: str,
    *,
    window_minutes: int,
    max_bytes: int = 2_000_000,
) -> XrayErrorSummary:
    path = Path(log_path)
    window = max(1, int(window_minutes))
    since_ts = int(time.time()) - window * 60
    if not path.exists():
        return XrayErrorSummary(
            log_path=str(path),
            window_minutes=window,
            since_ts=since_ts,
            total=0,
            top_levels=[],
            top_signatures=[],
            top_remote_ips=[],
            samples=[],
            file_missing=True,
        )
    try:
        text = _tail_text(path, max_bytes=max_bytes)
    except OSError as exc:
        return XrayErrorSummary(
            log_path=str(path),
            window_minutes=window,
            since_ts=since_ts,
            total=0,
            top_levels=[],
            top_signatures=[],
            top_remote_ips=[],
            samples=[],
            read_error=str(exc),
        )
    return summarize_xray_error_lines(
        text.splitlines(),
        log_path=str(path),
        window_minutes=window,
    )


def format_xray_quality_report(summary: XrayErrorSummary, *, show: int = 8) -> str:
    show = max(1, int(show))
    lines = [
        "📡 Xray quality report",
        f"Log: {summary.log_path}",
        f"Window: {summary.window_minutes} min",
    ]
    if summary.file_missing:
        lines.append("Result: log file not found")
        return "\n".join(lines)
    if summary.read_error:
        lines.append(f"Result: read error: {summary.read_error}")
        return "\n".join(lines)

    lines.append(f"Errors/warnings: {summary.total}")
    if summary.total == 0:
        lines.append("Result: OK")
        return "\n".join(lines)

    if summary.top_levels:
        lines.append("\nLevels:")
        lines.extend(f"- {level}: {count}" for level, count in summary.top_levels[:show])
    if summary.top_signatures:
        lines.append("\nTop signatures:")
        lines.extend(f"- {count}x {signature}" for signature, count in summary.top_signatures[:show])
    if summary.top_remote_ips:
        lines.append("\nTop remote IPs:")
        lines.extend(f"- {ip}: {count}" for ip, count in summary.top_remote_ips[:show])
    if summary.samples:
        lines.append("\nSamples:")
        lines.extend(f"- {sample}" for sample in summary.samples[: min(3, show)])
    return "\n".join(lines)
