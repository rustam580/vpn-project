from __future__ import annotations

from wbstream_livekit_tuning_sweep import build_cases, parse_float_list, parse_int_list


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
