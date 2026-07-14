"""
Unit tests for M3U digest exclude-filter safe_regex migration (bd-eio04.17).

The digest runs user-supplied exclude regexes against group/stream names.
This suite locks in the graceful-fallback contract: on timeout, oversize
pattern, or invalid pattern, the row must be treated as NOT matched (i.e.
it passes through the exclude filter) — dropping rows on a regex failure
would silently discard changes the operator may need to see.

The adversarial-path tests exercise the same inline filter block used in
M3UDigestTask.execute() (see the module-level import of the live function
below), so a regression in the production code path fails here rather than
hiding behind a reimplemented helper.
"""
import logging
import time

import pytest

import safe_regex
from models import M3UChangeLog


# ---------------------------------------------------------------------------
# These adversarial tests drive the REAL production exclude filter
# (tasks.m3u_digest.apply_exclude_filters — the same code path
# M3UDigestTask.execute() runs), so a regression in the production timeout /
# oversize / invalid-pattern handling fails here directly rather than hiding
# behind a reimplemented helper.
# ---------------------------------------------------------------------------


def _apply_exclude_filters(changes, group_patterns_raw, stream_patterns_raw):
    """Call the real production exclude filter from tasks.m3u_digest."""
    from tasks.m3u_digest import apply_exclude_filters

    return apply_exclude_filters(changes, group_patterns_raw, stream_patterns_raw)


def _make_change(session, change_type, group_name, stream_names=None, count=None):
    c = M3UChangeLog(
        m3u_account_id=1,
        change_type=change_type,
        group_name=group_name,
        count=count or (len(stream_names) if stream_names else 1),
    )
    if stream_names:
        c.set_stream_names(stream_names)
    session.add(c)
    session.commit()
    return c


# ---------------------------------------------------------------------------
# Sanity: the happy path still behaves after the migration.
# ---------------------------------------------------------------------------


class TestHappyPathAfterMigration:
    def test_normal_group_pattern_still_filters(self, test_session):
        c1 = _make_change(test_session, "group_added", "ESPN+ Events")
        c2 = _make_change(test_session, "group_added", "News")
        result = _apply_exclude_filters([c1, c2], [r"ESPN\+"], [])
        assert [c.group_name for c in result] == ["News"]

    def test_normal_stream_pattern_still_filters(self, test_session):
        c1 = _make_change(
            test_session, "streams_added", "Sports",
            stream_names=["PPV Event 1", "ESPN HD"],
        )
        result = _apply_exclude_filters([c1], [], [r"PPV"])
        assert len(result) == 1
        assert result[0].get_stream_names() == ["ESPN HD"]


# ---------------------------------------------------------------------------
# Adversarial patterns — graceful fallback contract.
# ---------------------------------------------------------------------------


class TestAdversarialPatterns:
    # Aligned with safe_regex's own empirical timeout fixture.
    REAL_REDOS_PATTERN = r"(a|aa)+b"
    WALL_CLOCK_BUDGET_MS = 500  # 5x the 100ms timeout_ms default

    def test_redos_group_pattern_row_passes_through(self, test_session, caplog):
        """
        ReDoS group pattern → safe_regex.search returns None on timeout → no
        match → row is NOT excluded. Row survives, WARN is logged, and the
        filter completes well within the wall-clock budget.
        """
        group_name = "a" * 30 + "!"  # triggers catastrophic backtracking
        change = _make_change(test_session, "group_added", group_name)

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            result = _apply_exclude_filters([change], [self.REAL_REDOS_PATTERN], [])
            elapsed_ms = (time.monotonic() - start) * 1000

        assert len(result) == 1, "on ReDoS timeout, row must pass through"
        assert result[0].group_name == group_name
        assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS, (
            f"digest filter elapsed {elapsed_ms:.1f}ms exceeded "
            f"budget {self.WALL_CLOCK_BUDGET_MS}ms"
        )
        assert any(
            "[SAFE_REGEX]" in rec.getMessage() and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), "expected [SAFE_REGEX] WARN on timeout"

    def test_redos_stream_pattern_row_passes_through(self, test_session, caplog):
        """ReDoS stream pattern → all stream names survive the filter."""
        stream_name = "a" * 30 + "!"
        change = _make_change(
            test_session, "streams_added", "Sports",
            stream_names=[stream_name],
        )

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            result = _apply_exclude_filters([change], [], [self.REAL_REDOS_PATTERN])
            elapsed_ms = (time.monotonic() - start) * 1000

        assert len(result) == 1, "row must pass through on stream-pattern timeout"
        assert result[0].get_stream_names() == [stream_name]
        assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS

    def test_oversize_pattern_is_skipped(self, test_session, caplog):
        """Oversize pattern → compile raises PatternTooLongError → skipped, row not excluded."""
        oversize = "a" * 501
        change = _make_change(test_session, "group_added", "aaa")

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = _apply_exclude_filters([change], [oversize], [])

        assert len(result) == 1
        assert any("[SAFE_REGEX]" in rec.getMessage() for rec in caplog.records)

    def test_invalid_pattern_is_skipped(self, test_session):
        """Syntactically invalid pattern → compile raises SafeRegexError → skipped."""
        change = _make_change(test_session, "group_added", "Sports")

        result = _apply_exclude_filters([change], [r"(unclosed"], [])
        assert len(result) == 1, "invalid pattern must not crash the digest"


# ---------------------------------------------------------------------------
# Contract: the production code path must call safe_regex, not stdlib re.
# ---------------------------------------------------------------------------


class TestProductionUsesSafeRegex:
    def test_production_module_imports_safe_regex(self):
        """
        Regression: if someone reverts the migration, this test fails loudly.
        The digest task file must import safe_regex at module scope.
        """
        import tasks.m3u_digest as digest_module
        assert hasattr(digest_module, "safe_regex"), (
            "tasks.m3u_digest must import safe_regex (bd-eio04.17)"
        )

    def test_production_filter_uses_safe_regex(self):
        """
        Sanity check that the real exclude filter still calls the safe_regex
        API (per-invocation ReDoS timeout / oversize-pattern rejection).
        The adversarial tests above drive apply_exclude_filters directly, so a
        revert to bare ``re`` would already fail them; this source-level check
        pins the intent with a clear message.
        """
        import inspect
        from tasks.m3u_digest import apply_exclude_filters

        src = inspect.getsource(apply_exclude_filters)
        assert "safe_regex.compile" in src, (
            "apply_exclude_filters no longer calls safe_regex.compile — "
            "the bd-eio04.17 migration has been reverted"
        )
        assert "safe_regex.search" in src, (
            "apply_exclude_filters no longer calls safe_regex.search — "
            "the bd-eio04.17 migration has been reverted"
        )
