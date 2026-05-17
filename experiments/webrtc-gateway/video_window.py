from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowStats:
    total_chunks: int
    acked_chunks: int
    frames_sent: int
    retransmits: int
    window_size: int
    retry_timeout_sec: float


class SlidingWindowSender:
    def __init__(self, *, total_chunks: int, window_size: int, retry_timeout_sec: float) -> None:
        if total_chunks <= 0:
            raise ValueError("total_chunks must be positive")
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        if retry_timeout_sec <= 0:
            raise ValueError("retry_timeout_sec must be positive")
        self.total_chunks = total_chunks
        self.window_size = min(window_size, total_chunks)
        self.retry_timeout_sec = retry_timeout_sec
        self.acked: set[int] = set()
        self.inflight: dict[int, float] = {}
        self._next_seq = 0
        self.frames_sent = 0
        self.retransmits = 0

    @property
    def complete(self) -> bool:
        return len(self.acked) == self.total_chunks

    @property
    def stats(self) -> WindowStats:
        return WindowStats(
            total_chunks=self.total_chunks,
            acked_chunks=len(self.acked),
            frames_sent=self.frames_sent,
            retransmits=self.retransmits,
            window_size=self.window_size,
            retry_timeout_sec=self.retry_timeout_sec,
        )

    def update_ack(self, received: set[int] | frozenset[int]) -> int:
        before = len(self.acked)
        for seq in received:
            if 0 <= seq < self.total_chunks:
                self.acked.add(seq)
                self.inflight.pop(seq, None)
        return len(self.acked) - before

    def due(self, now: float) -> list[int]:
        if self.complete:
            return []

        due: list[int] = []
        for seq, sent_at in sorted(self.inflight.items()):
            if seq in self.acked:
                continue
            if now - sent_at >= self.retry_timeout_sec:
                due.append(seq)

        open_slots = max(0, self.window_size - len([seq for seq in self.inflight if seq not in self.acked]))
        while open_slots > 0 and self._next_seq < self.total_chunks:
            seq = self._next_seq
            self._next_seq += 1
            if seq in self.acked:
                continue
            due.append(seq)
            open_slots -= 1
        return due

    def mark_sent(self, seq: int, now: float) -> None:
        if not 0 <= seq < self.total_chunks:
            raise ValueError("seq out of range")
        if seq in self.acked:
            return
        if seq in self.inflight:
            self.retransmits += 1
        self.inflight[seq] = now
        self.frames_sent += 1

    def mark_duplicate_sent(self, seq: int) -> None:
        if not 0 <= seq < self.total_chunks:
            raise ValueError("seq out of range")
        if seq in self.acked:
            return
        self.frames_sent += 1
