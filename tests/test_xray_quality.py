from __future__ import annotations

import time

from src.vpnbot.xray_quality import (
    format_xray_quality_report,
    summarize_xray_error_lines,
    summarize_xray_error_log,
)


def test_xray_quality_summary_counts_recent_errors_and_normalizes_signatures() -> None:
    now = int(time.mktime(time.strptime("2026-01-10 08:30:00", "%Y-%m-%d %H:%M:%S")))
    lines = [
        "2026/01/10 08:25:00 [Warning] [123] proxy/vless/inbound: connection ends > from tcp:1.2.3.4:53421 rejected",
        "2026/01/10 08:25:20 [Warning] [456] proxy/vless/inbound: connection ends > from tcp:1.2.3.4:53422 rejected",
        "2026/01/10 08:10:00 [Error] proxy/vless/inbound: old error from tcp:5.6.7.8:443",
    ]

    summary = summarize_xray_error_lines(
        lines,
        log_path="/var/log/xray/error.log",
        window_minutes=10,
        now_ts=now,
    )

    assert summary.total == 2
    assert summary.top_levels == [("warning", 2)]
    assert summary.top_remote_ips == [("1.2.3.4", 2)]
    assert summary.top_signatures[0][1] == 2
    assert "<ip>" in summary.top_signatures[0][0]


def test_xray_quality_missing_file_report(local_tmp_path) -> None:
    path = local_tmp_path / "missing.log"

    summary = summarize_xray_error_log(str(path), window_minutes=15)
    text = format_xray_quality_report(summary)

    assert summary.file_missing is True
    assert "log file not found" in text


def test_xray_quality_ok_report_for_empty_window() -> None:
    summary = summarize_xray_error_lines(
        [],
        log_path="/tmp/error.log",
        window_minutes=15,
        now_ts=1_768_000_000,
    )

    assert "Result: OK" in format_xray_quality_report(summary)
