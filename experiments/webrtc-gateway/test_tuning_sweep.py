from __future__ import annotations

from wbstream_livekit_tuning_sweep import (
    SweepRecord,
    aggregate_records,
    build_cases,
    parse_float_list,
    parse_int_list,
)


def test_parse_int_list():
    assert parse_int_list("1, 2,3") == [1, 2, 3]


def test_parse_float_list():
    assert parse_float_list("1, 2.5") == [1.0, 2.5]


def test_build_cases_order_and_max_runs():
    cases = build_cases(
        payloads=[512, 1024],
        windows=[2, 4],
        retries=[1.5],
        fps_values=[8],
        ack_fps_values=[4],
        max_runs=3,
    )
    assert [(case.payload_bytes, case.window_size) for case in cases] == [(512, 2), (512, 4), (1024, 2)]
    assert {case.codec for case in cases} == {"binary"}
    assert {case.data_repeats for case in cases} == {1}


def test_build_cases_can_expand_codecs():
    cases = build_cases(
        payloads=[512],
        windows=[4],
        retries=[2.5],
        fps_values=[8],
        ack_fps_values=[4],
        codecs=["binary", "tile2"],
        data_repeats_values=[1, 2],
    )
    assert [(case.codec, case.data_repeats) for case in cases] == [
        ("binary", 1),
        ("binary", 2),
        ("tile2", 1),
        ("tile2", 2),
    ]


def test_aggregate_records_groups_by_case_and_calculates_stats():
    records = [
        SweepRecord(
            ok=True,
            case={
                "payload_bytes": 1024,
                "window_size": 4,
                "retry_timeout_sec": 2.5,
                "fps": 8,
                "ack_fps": 4,
                "codec": "binary",
                "data_repeats": 1,
            },
            repeat_index=1,
            elapsed_ms=10000,
            throughput_bps=100.0,
            retransmits=0,
        ),
        SweepRecord(
            ok=True,
            case={
                "payload_bytes": 1024,
                "window_size": 4,
                "retry_timeout_sec": 2.5,
                "fps": 8,
                "ack_fps": 4,
                "codec": "binary",
                "data_repeats": 1,
            },
            repeat_index=2,
            elapsed_ms=12000,
            throughput_bps=80.0,
            retransmits=2,
        ),
        SweepRecord(
            ok=False,
            case={
                "payload_bytes": 1024,
                "window_size": 4,
                "retry_timeout_sec": 2.5,
                "fps": 8,
                "ack_fps": 4,
                "codec": "binary",
                "data_repeats": 1,
            },
            repeat_index=3,
            error="timeout",
        ),
    ]

    aggregates = aggregate_records(records)

    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert aggregate.total_runs == 3
    assert aggregate.successful_runs == 2
    assert aggregate.failed_runs == 1
    assert aggregate.throughput_min_bps == 80.0
    assert aggregate.throughput_median_bps == 90.0
    assert aggregate.throughput_p95_bps == 100.0
    assert aggregate.throughput_max_bps == 100.0
    assert aggregate.elapsed_median_ms == 11000.0
    assert aggregate.retransmits_median == 1.0
