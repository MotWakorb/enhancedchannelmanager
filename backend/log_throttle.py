"""Lightweight monotonic-clock log throttle (bead enhancedchannelmanager-vkktd.1).

Several task-scheduler decision points are re-evaluated on EVERY ~60s tick.
Logging them unconditionally would bury the real signal under per-tick spam,
but leaving them silent is exactly the trap the ``vkktd`` epic was filed for:
a gated-off task that never fires and never says so, costing a multi-hour
diagnosis. This helper gates a log call to at most once per ``window`` seconds
per ``key`` — the FIRST occurrence of a condition surfaces immediately and
subsequent identical ticks stay quiet until the window rolls over.

Keyed by an arbitrary string (by convention ``"<reason>:<task_id>"``) so
independent conditions and tasks throttle independently — a persistently
parent-disabled task cannot silence a different task's warning.

The clock is ``time.monotonic()`` so a wall-clock jump (NTP step, DST) can
never widen or collapse the window.
"""
from __future__ import annotations

import time
from typing import Optional

# Default: at most one line per key per 5 minutes. The scheduler ticks roughly
# every 60s, so a persistent condition collapses ~5 ticks into a single line.
DEFAULT_WINDOW_SECONDS = 300.0

_last_logged_at: dict[str, float] = {}


def should_log(key: str, window: float = DEFAULT_WINDOW_SECONDS) -> bool:
    """Return ``True`` at most once per ``window`` seconds for ``key``.

    The first call for a key (or the first after ``window`` seconds elapse)
    returns ``True`` and arms the window; calls within the window return
    ``False``. Callers gate a log statement on the return value.
    """
    now = time.monotonic()
    last = _last_logged_at.get(key)
    if last is not None and (now - last) < window:
        return False
    _last_logged_at[key] = now
    return True


def reset(key: Optional[str] = None) -> None:
    """Clear throttle state — TESTS ONLY.

    ``reset()`` clears everything; ``reset(key)`` clears one key. The
    module-level state persists across test functions, so a test that asserts
    a throttled line appears would otherwise be suppressed by a sibling test
    that already armed the same window. Production code never calls this.
    """
    if key is None:
        _last_logged_at.clear()
    else:
        _last_logged_at.pop(key, None)
