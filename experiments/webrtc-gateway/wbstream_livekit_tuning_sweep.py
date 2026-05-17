from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from wbstream_livekit_frame_window import SUPPORTED_CODECS, wbstream_video_window_probe

CaseValue = int | float | str


@dataclass(frozen=True)
class SweepCase:
    payload_bytes: int
    window_size: int
    retry_timeout_sec: float
    fps: int
    ack_fps: int
    codec: str
    data_repeats: int

    def label(self) -> str:
        return (
            f"payload={self.payload_bytes} window={self.window_size} "
            f"retry={self.retry_timeout_sec:g}s fps={self.fps} ack_fps={self.ack_fps} "
            f"codec={self.codec} data_repeats={self.data_repeats}"
        )


@dataclass(frozen=True)
class SweepRecord:
    ok: bool
    case: dict[str, CaseValue]
    repeat_index: int
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
class SweepAggregate:
    case: dict[str, CaseValue]
    total_runs: int
    successful_runs: int
    failed_runs: int
    throughput_min_bps: float | None
    throughput_median_bps: float | None
    throughput_p95_bps: float | None
    throughput_max_bps: float | None
    elapsed_median_ms: float | None
    retransmits_median: float | None


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
    best_aggregate: SweepAggregate | None
    aggregates: list[SweepAggregate]
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
            "best_aggregate": asdict(self.best_aggregate) if self.best_aggregate else None,
            "aggregates": [asdict(aggregate) for aggregate in self.aggregates],
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
    codecs: list[str] | None = None,
    data_repeats_values: list[int] | None = None,
    max_runs: int | None = None,
) -> list[SweepCase]:
    codecs = codecs or ["binary"]
    data_repeats_values = data_repeats_values or [1]
    unsupported = [codec for codec in codecs if codec not in SUPPORTED_CODECS]
    if unsupported:
        raise ValueError(f"unsupported codecs: {', '.join(unsupported)}")
    invalid_repeats = [value for value in data_repeats_values if value <= 0]
    if invalid_repeats:
        raise ValueError("data repeats must be positive")
    cases = [
        SweepCase(
            payload_bytes=payload,
            window_size=window,
            retry_timeout_sec=retry,
            fps=fps,
            ack_fps=ack_fps,
            codec=codec,
            data_repeats=data_repeats,
        )
        for payload in payloads
        for window in windows
        for retry in retries
        for fps in fps_values
        for ack_fps in ack_fps_values
        for codec in codecs
        for data_repeats in data_repeats_values
    ]
    if max_runs is not None:
        cases = cases[:max_runs]
    return cases


def _case_dict(case: SweepCase) -> dict[str, CaseValue]:
    return {
        "payload_bytes": case.payload_bytes,
        "window_size": case.window_size,
        "retry_timeout_sec": case.retry_timeout_sec,
        "fps": case.fps,
        "ack_fps": case.ack_fps,
        "codec": case.codec,
        "data_repeats": case.data_repeats,
    }


def _best_record(records: list[SweepRecord]) -> SweepRecord | None:
    successful = [record for record in records if record.ok and record.throughput_bps is not None]
    if not successful:
        return None
    return max(successful, key=lambda record: record.throughput_bps or 0.0)


def _percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _case_key(case: dict[str, CaseValue]) -> str:
    return json.dumps(case, sort_keys=True, separators=(",", ":"))


def aggregate_records(records: list[SweepRecord]) -> list[SweepAggregate]:
    by_case: dict[str, list[SweepRecord]] = {}
    case_by_key: dict[str, dict[str, CaseValue]] = {}
    for record in records:
        key = _case_key(record.case)
        by_case.setdefault(key, []).append(record)
        case_by_key[key] = record.case

    aggregates: list[SweepAggregate] = []
    for key in sorted(by_case):
        group = by_case[key]
        successful = [record for record in group if record.ok]
        throughputs = [
            float(record.throughput_bps)
            for record in successful
            if record.throughput_bps is not None
        ]
        elapsed = [
            float(record.elapsed_ms)
            for record in successful
            if record.elapsed_ms is not None
        ]
        retransmits = [
            float(record.retransmits)
            for record in successful
            if record.retransmits is not None
        ]
        aggregates.append(
            SweepAggregate(
                case=case_by_key[key],
                total_runs=len(group),
                successful_runs=len(successful),
                failed_runs=len(group) - len(successful),
                throughput_min_bps=min(throughputs) if throughputs else None,
                throughput_median_bps=_median(throughputs),
                throughput_p95_bps=_percentile_nearest_rank(throughputs, 95),
                throughput_max_bps=max(throughputs) if throughputs else None,
                elapsed_median_ms=_median(elapsed),
                retransmits_median=_median(retransmits),
            )
        )
    return aggregates


def _best_aggregate(aggregates: list[SweepAggregate]) -> SweepAggregate | None:
    successful = [
        aggregate
        for aggregate in aggregates
        if aggregate.successful_runs > 0 and aggregate.throughput_median_bps is not None
    ]
    if not successful:
        return None
    return max(successful, key=lambda aggregate: aggregate.throughput_median_bps or 0.0)


async def run_sweep(
    room: str,
    *,
    cases: list[SweepCase],
    repeats: int,
    timeout_sec: float,
    pause_sec: float,
) -> SweepSummary:
    started = time.time()
    perf_started = time.perf_counter()
    records: list[SweepRecord] = []

    total_runs = len(cases) * repeats
    run_index = 0
    for case_index, case in enumerate(cases, start=1):
        for repeat_index in range(1, repeats + 1):
            run_index += 1
            print(
                f"[{run_index}/{total_runs}] case={case_index}/{len(cases)} "
                f"repeat={repeat_index}/{repeats} {case.label()}",
                flush=True,
            )
            try:
                result = await wbstream_video_window_probe(
                    room,
                    payload_bytes=case.payload_bytes,
                    timeout_sec=timeout_sec,
                    fps=case.fps,
                    ack_fps=case.ack_fps,
                    window_size=case.window_size,
                    retry_timeout_sec=case.retry_timeout_sec,
                    codec=case.codec,
                    data_repeats=case.data_repeats,
                )
                data = result.safe_dict()
                record = SweepRecord(
                    ok=True,
                    case=_case_dict(case),
                    repeat_index=repeat_index,
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
                record = SweepRecord(
                    ok=False,
                    case=_case_dict(case),
                    repeat_index=repeat_index,
                    error=f"{type(exc).__name__}: {exc}",
                )
                print(f"  fail {record.error}", flush=True)
            records.append(record)
            if run_index != total_runs and pause_sec > 0:
                await asyncio.sleep(pause_sec)

    best = _best_record(records)
    aggregates = aggregate_records(records)
    best_aggregate = _best_aggregate(aggregates)
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
        best_aggregate=best_aggregate,
        aggregates=aggregates,
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
    parser.add_argument("--codecs", default="binary", help="comma-separated codecs: binary,tile2")
    parser.add_argument("--data-repeats", default="1", help="comma-separated data-frame repeat counts")
    parser.add_argument("--timeout-sec", type=float, default=150.0)
    parser.add_argument("--pause-sec", type=float, default=2.0)
    parser.add_argument("--repeats", type=int, default=1, help="runs per parameter case")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--json-out", help="optional output path for full JSON results")
    args = parser.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be >= 1")

    cases = build_cases(
        payloads=parse_int_list(args.payloads),
        windows=parse_int_list(args.windows),
        retries=parse_float_list(args.retries),
        fps_values=parse_int_list(args.fps),
        ack_fps_values=parse_int_list(args.ack_fps),
        codecs=[item.strip() for item in args.codecs.split(",") if item.strip()],
        data_repeats_values=parse_int_list(args.data_repeats),
        max_runs=args.max_runs,
    )
    summary = await run_sweep(
        args.room,
        cases=cases,
        repeats=args.repeats,
        timeout_sec=args.timeout_sec,
        pause_sec=args.pause_sec,
    )
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
