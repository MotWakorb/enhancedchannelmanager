"""
Benchmark: _sort_key hot path (bd-eio04.15).

Compares three variants on a 1000-stream fixture sorted N log N times:

1. Baseline — stdlib ``re.search`` per call (pre-migration behavior).
2. Migration (raw string) — ``safe_regex.search(pattern_str, ...)`` per
   call. Simulates a caller that forgets to precompile; pays the per-call
   compile cost.
3. Migration (compiled) — pre-compile via ``safe_regex.compile`` once,
   pass compiled pattern into the key function. This is what
   auto_creation_engine._run_rules now does.

The grooming SLA is <10% total sort-time overhead vs. baseline for the
compiled variant. The raw-string variant is expected to be slower (that's
why we added the compile-once mitigation) but should still be within an
order of magnitude.

Run with::

    docker exec ecm-ecm-1 python -m tests.unit.bench_sort_key_safe_regex

This file is NOT a pytest test — it's a standalone benchmark. Keeping it
under ``tests/unit/`` with a ``bench_`` prefix ensures pytest does not
collect it (pytest collects ``test_*.py``) while still versioning it
alongside the migration tests.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

# Support running from repo root or from backend/.
_backend = Path(__file__).resolve().parent.parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import safe_regex
from auto_creation_evaluator import StreamContext


_PATTERN = r"Race (\d+)"
_N = 1000


def _build_fixture(n: int = _N) -> list[StreamContext]:
    """1000 streams with varied names: half benign-matching, half not."""
    streams = []
    for i in range(n):
        if i % 2 == 0:
            name = f"Race {i:05d} HD"
        else:
            name = f"ESPN HD #{i:05d}"
        streams.append(StreamContext(stream_id=i, stream_name=name))
    return streams


def _baseline_sort_key(stream: StreamContext, pattern: str) -> tuple:
    try:
        m = re.search(pattern, stream.stream_name)
    except re.error:
        return (-1, 0, "")
    if m and m.groups():
        captured = m.group(1)
        try:
            return (0, float(captured), captured)
        except (ValueError, TypeError):
            return (0, 0, captured)
    return (-1, 0, "")


def _safe_regex_sort_key_string(stream: StreamContext, pattern: str) -> tuple:
    m = safe_regex.search(pattern, stream.stream_name)
    if m is not None and m.groups():
        captured = m.group(1)
        try:
            return (0, float(captured), captured)
        except (ValueError, TypeError):
            return (0, 0, captured)
    return (-1, 0, "")


def _safe_regex_sort_key_compiled(stream: StreamContext, compiled) -> tuple:
    m = safe_regex.search(compiled, stream.stream_name)
    if m is not None and m.groups():
        captured = m.group(1)
        try:
            return (0, float(captured), captured)
        except (ValueError, TypeError):
            return (0, 0, captured)
    return (-1, 0, "")


def _bench(label: str, fn, repeat: int = 5) -> float:
    best = float("inf")
    for _ in range(repeat):
        streams = _build_fixture()
        start = time.perf_counter()
        streams.sort(key=fn)
        elapsed = time.perf_counter() - start
        best = min(best, elapsed)
    print(f"  {label}: best-of-{repeat} = {best * 1000:.2f} ms")
    return best


def main() -> None:
    print(f"Sort-key benchmark — {_N} streams, pattern={_PATTERN!r}\n")

    print("Baseline (stdlib re.search per call):")
    baseline = _bench("stdlib re", lambda s: _baseline_sort_key(s, _PATTERN))

    print("\nMigration, raw-string (safe_regex.search compiles on each call):")
    naive = _bench(
        "safe_regex str",
        lambda s: _safe_regex_sort_key_string(s, _PATTERN),
    )

    print("\nMigration, pre-compiled (compile once, reuse per call):")
    compiled = safe_regex.compile(_PATTERN)
    fast = _bench(
        "safe_regex compiled",
        lambda s: _safe_regex_sort_key_compiled(s, compiled),
    )

    print("\nOverhead vs. baseline:")
    print(f"  safe_regex str:      {100 * (naive - baseline) / baseline:+.1f}%")
    print(f"  safe_regex compiled: {100 * (fast - baseline) / baseline:+.1f}%")
    print("\nGrooming SLA: compiled overhead must be <10% of baseline.")


if __name__ == "__main__":
    main()
