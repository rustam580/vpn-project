from __future__ import annotations

from video_window import SlidingWindowSender


def test_sliding_window_initially_fills_window():
    sender = SlidingWindowSender(total_chunks=5, window_size=2, retry_timeout_sec=3.0)
    assert sender.due(0.0) == [0, 1]
    sender.mark_sent(0, 0.0)
    sender.mark_sent(1, 0.0)
    assert sender.due(1.0) == []


def test_sliding_window_opens_slots_after_ack():
    sender = SlidingWindowSender(total_chunks=5, window_size=2, retry_timeout_sec=3.0)
    for seq in sender.due(0.0):
        sender.mark_sent(seq, 0.0)
    assert sender.update_ack({0}) == 1
    assert sender.due(0.5) == [2]


def test_sliding_window_retries_timed_out_inflight():
    sender = SlidingWindowSender(total_chunks=3, window_size=2, retry_timeout_sec=3.0)
    for seq in sender.due(0.0):
        sender.mark_sent(seq, 0.0)
    assert sender.due(2.9) == []
    assert sender.due(3.0) == [0, 1]
    sender.mark_sent(0, 3.0)
    assert sender.stats.retransmits == 1


def test_sliding_window_ignores_invalid_ack_and_completes():
    sender = SlidingWindowSender(total_chunks=2, window_size=2, retry_timeout_sec=3.0)
    assert sender.update_ack({0, 1, 999}) == 2
    assert sender.complete is True
    assert sender.due(10.0) == []
    assert sender.stats.acked_chunks == 2
