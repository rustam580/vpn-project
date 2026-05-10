"""Helpers to launch fire-and-forget asyncio tasks safely.

`asyncio.create_task` returns a Task that the event loop only weakly references,
so without a strong reference the GC may cancel it mid-flight and any exception
is silently lost. `spawn` keeps a strong reference for the task lifetime and
discards it on completion.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine, Set

_BACKGROUND_TASKS: Set[asyncio.Task] = set()


def spawn(coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
    """Schedule `coro` and keep a strong reference until it completes.

    Logs any unhandled exception via `logging.exception` so background failures
    are not swallowed.
    """
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logging.exception("Background task %r failed", t.get_name(), exc_info=exc)

    task.add_done_callback(_on_done)
    return task
