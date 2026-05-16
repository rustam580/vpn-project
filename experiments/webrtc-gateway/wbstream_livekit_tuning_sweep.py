from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from wbstream_livekit_frame_window import wbstream_video_window_probe


@dataclass(frozen=True)
class SweepCase:
    payload_bytes: int
    window_size: int
    retry_timeout_sec: float
    fps: int
    ack_fps: int

    def label(self) -> str:
        return (
            f"payload={self.payload_bytes} window={self.window_size} "
            f"retry={self.retry_timeout_sec:g}s fps={self.fps} ack_fps={self.ack_fps}"
        )


@dataclass(frozen=True)
class SweepRecord:
    ok: bool
    case: dict[str, int | float]
    elapsed_ms: int | None = None
    throughput_bps: float | None = None
    encoded_frames: int | None = None
    data_frames_sent: int | None = None
    retransmits: int | None = None
    ack_frames_sent: int | None = None
    data_decode_attempts: int | None = None
    ack_decode_attempts: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class SweepSummary:
    ok: bool
    room: str
    started_at: float
    elapsed_ms: int
    total_runs: int
    successful_runs: int
    failed_runs: int
    best: SweepRecord | None
    records: list[SweepRecord]

    def safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "room": self.room,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "best": asdict(self.best) if self.best else None,
            "records": [asdict(record) for record in self.records],
        }


def parse_int_list(value: str) -> list[int]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    if not items:
        raise ValueError("list must not be empty")
    return [int(item) for item in items]


def parse_float_list(value: str) -> list[float]:
    items = [part.strip() for part in value.split(",") if part.strip()]
    if not items:
        raise ValueError("list must not be empty")
    return [float(item) for item in items]


def build_cases(
    *,
    payloads: list[int],
    windows: list[int],
    retries: list[float],
    fps_values: list[int],
    ack_fps_values: list[int],
    max_runs: int | None = None,
) -> list[SweepCase]:
    cases = [
        SweepCase(
            payload_bytes=payload,
            window_size=window,
            retry_timeout_sec=retry,
            fps=fps,
            ack_fps=ack_fps,
        )
        for payload in payloads
        for window in windows
        for retry in retries
        for fps in fps_values
        for ack_fps in ack_fps_values
    ]
    if max_runs is not None:
        cases = cases[:max_runs]
    return cases


def _case_dict(case: SweepCase) -> dict[str, int | float]:
    return {
        "payload_bytes": case.payload_bytes,
        "window_size": case.window_size,
        "retry_timeout_sec": case.retry_timeout_sec,
        "fps": case.fps,
        "ack_fps": case.ack_fps,
    }


def _best_record(records: list[SweepRecord]) -> SweepRecord | None:
    successful = [record for record in records if record.ok and record.throughput_bps is not None]
    if not successful:
        return None
    return max(successful, key=lambda record: record.throughput_bps or 0.0)


async def run_sweep(
    room: str,
    *,
    cases: list[SweepCase],
    timeout_sec: float,
    pause_sec: float,
) -> SweepSummary:
    started = time.time()
    perf_started = time.perf_counter()
    records: list[SweepRecord] = []

    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.label()}", flush=True)
        try:
            result = await wbstream_video_window_probe(
                room,
                payload_bytes=case.payload_bytes,
                timeout_sec=timeout_sec,
                fps=case.fps,
                ack_fps=case.ack_fps,
                window_size=case.window_size,
                retry_timeout_sec=case.retry_timeout_sec,
            )
            data = result.safe_dict()
            record = SweepRecord(
                ok=True,
                case=_case_dict(case),
                elapsed_ms=int(data["elapsed_ms"]),
                throughput_bps=float(data["throughput_bps"]),
                encoded_frames=int(data["encoded_frames"]),
                data_frames_sent=int(data["data_frames_sent"]),
                retransmits=int(data["retransmits"]),
                ack_frames_sent=int(data["ack_frames_sent"]),
                data_decode_attempts=int(data["data_decode_attempts"]),
                ack_decode_attempts=int(data["ack_decode_attempts"]),
            )
            print(
                f"  ok throughput={record.throughput_bps:.2f} B/s "
                f"elapsed={record.elapsed_ms}ms retransmits={record.retransmits}",
                flush=True,
            )
        except Exception as exc:
            record = SweepRecord(ok=False, case=_case_dict(case), error=f"{type(exc).__name__}: {exc}")
            print(f"  fail {record.error}", flush=True)
        records.append(record)
        if index != len(cases) and pause_sec > 0:
            await asyncio.sleep(pause_sec)

    best = _best_record(records)
    elapsed_ms = int((time.perf_counter() - perf_started) * 1000)
    successful = len([record for record in records if record.ok])
    return SweepSummary(
        ok=successful == len(records),
        room=room,
        started_at=started,
        elapsed_ms=elapsed_ms,
        total_runs=len(records),
        successful_runs=successful,
        failed_runs=len(records) - successful,
        best=best,
        records=records,
    )


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Run a small WB Stream video carrier tuning sweep")
    parser.add_argument("room", help="WB Stream room ID or URL")
    parser.add_argument("--payloads", default="1024", help="comma-separated payload sizes in bytes")
    parser.add_argument("--windows", default="2,4", help="comma-separated sliding window sizes")
    parser.add_argument("--retries", default="2.5", help="comma-separated retry timeouts in seconds")
    parser.add_argument("--fps", default="8", help="comma-separated data FPS values")
    parser.add_argument("--ack-fps", default="4", help="comma-separated ACK FPS values")
    parser.add_argument("--timeout-sec", type=float, default=150.0)
    parser.add_argument("--pause-sec", type=float, default=2.0)
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--json-out", help="optional output path for full JSON results")
    args = parser.parse_args()

    cases = build_cases(
        payloads=parse_int_list(args.payloads),
        windows=parse_int_list(args.windows),
        retries=parse_float_list(args.retries),
        fps_values=parse_int_list(args.fps),
        ack_fps_values=parse_int_list(args.ack_fps),
        max_runs=args.max_runs,
    )
    summary = await run_sweep(args.room, cases=cases, timeout_sec=args.timeout_sec, pause_sec=args.pause_sec)
    payload = summary.safe_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0 if summary.successful_runs > 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
