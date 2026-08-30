"""
Unit tests for StreamProber._rewrite_url_for_profile's safe_regex migration
(bd-eio04.17).

The rewrite runs user-supplied regex against stream URLs. This test suite
locks in the graceful-fallback contract: on timeout, oversize pattern, or
invalid pattern, the original URL must be returned unchanged — probing the
source directly is always preferable to blocking a probe on an attacker- or
misconfiguration-supplied pattern.
"""
import logging
import time
from unittest.mock import MagicMock

from stream_prober import StreamProber


def _make_prober() -> StreamProber:
    """Build a StreamProber with minimal defaults for testing pure methods."""
    return StreamProber(client=MagicMock(), probe_timeout=1)


def _profile(search_pattern: str, replace_pattern: str = "",
             *, profile_id: int = 99, is_default: bool = False) -> dict:
    return {
        "id": profile_id,
        "is_default": is_default,
        "search_pattern": search_pattern,
        "replace_pattern": replace_pattern,
    }


# ---------------------------------------------------------------------------
# Happy-path: ordinary rewrites still work after the migration.
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_rewrites_when_pattern_matches(self):
        prober = _make_prober()
        result = prober._rewrite_url_for_profile(
            "http://src.example.com/stream",
            _profile(r"src\.example\.com", "cdn.example.com"),
        )
        assert result == "http://cdn.example.com/stream"

    def test_returns_original_when_no_pattern(self):
        prober = _make_prober()
        url = "http://src.example.com/stream"
        assert prober._rewrite_url_for_profile(url, _profile("")) == url

    def test_returns_original_for_default_profile(self):
        prober = _make_prober()
        url = "http://src.example.com/stream"
        # Default profile short-circuits before regex — any pattern is ignored.
        assert (
            prober._rewrite_url_for_profile(
                url,
                _profile(r"anything", "else", is_default=True),
            )
            == url
        )

    def test_rewrite_log_records_safe_event_without_pattern_values(self, caplog):
        prober = _make_prober()
        search_pattern = "sensitive-source-fragment"
        replace_pattern = "sensitive-target-fragment"

        with caplog.at_level(logging.DEBUG, logger="stream_prober"):
            result = prober._rewrite_url_for_profile(
                "http://source.invalid/sensitive-source-fragment/stream",
                _profile(search_pattern, replace_pattern, profile_id=77),
            )

        assert result == "http://source.invalid/sensitive-target-fragment/stream"
        assert "Profile 77: rewrote URL" in caplog.text
        assert search_pattern not in caplog.text
        assert replace_pattern not in caplog.text
        assert "source-fragment" not in caplog.text
        assert "target-fragment" not in caplog.text


# ---------------------------------------------------------------------------
# Adversarial: ReDoS, oversize, invalid pattern all fall back to original URL.
# ---------------------------------------------------------------------------


class TestAdversarialPatterns:
    # Same genuine-ReDoS fixture the safe_regex unit tests use to
    # empirically exercise the timeout plumbing (see test_safe_regex.py
    # TestTimeoutBehavior.REAL_REDOS_PATTERN).
    REAL_REDOS_PATTERN = r"(a|aa)+b"
    WALL_CLOCK_BUDGET_MS = 500  # 5x the 100ms timeout_ms default

    def test_redos_pattern_returns_original_url_within_budget(self, caplog):
        """Catastrophic-backtracking pattern → original URL, no stall, WARN logged."""
        prober = _make_prober()
        # Build a URL whose path forces the pattern into catastrophic
        # backtracking territory.
        url = "http://example.com/" + "a" * 30 + "!"

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            result = prober._rewrite_url_for_profile(
                url, _profile(self.REAL_REDOS_PATTERN, "X"),
            )
            elapsed_ms = (time.monotonic() - start) * 1000

        assert result == url, "on ReDoS timeout, URL must be returned unchanged"
        assert elapsed_ms < self.WALL_CLOCK_BUDGET_MS, (
            f"rewrite elapsed {elapsed_ms:.1f}ms exceeded "
            f"budget {self.WALL_CLOCK_BUDGET_MS}ms"
        )
        # safe_regex emits [SAFE_REGEX] WARN on timeout.
        assert any(
            "[SAFE_REGEX]" in rec.getMessage() and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), "expected [SAFE_REGEX] WARN on timeout"
        assert self.REAL_REDOS_PATTERN not in caplog.text
        assert "pattern_excerpt=" not in caplog.text

    def test_oversize_pattern_returns_original_url(self, caplog):
        """Pattern beyond the 500-char cap → original URL, WARN logged."""
        prober = _make_prober()
        oversize = "a" * 501
        url = "http://example.com/aaa"

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = prober._rewrite_url_for_profile(url, _profile(oversize, "X"))

        assert result == url
        assert any("[SAFE_REGEX]" in rec.getMessage() for rec in caplog.records)
        assert oversize[:50] not in caplog.text
        assert "pattern_excerpt=" not in caplog.text

    def test_invalid_pattern_returns_original_url(self, caplog):
        """Syntactically invalid pattern → original URL, no exception bubbles up."""
        prober = _make_prober()
        url = "http://example.com/stream"

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = prober._rewrite_url_for_profile(url, _profile(r"(unclosed"))

        assert result == url, "invalid regex must not propagate an exception"
        assert "(unclosed" not in caplog.text
        assert "pattern_excerpt=" not in caplog.text

    def test_invalid_replacement_template_logs_no_pattern_or_template_values(self, caplog):
        prober = _make_prober()
        pattern = r"(sensitive-pattern-fragment)"
        replacement = r"sensitive-template-fragment-\2"
        url = "http://example.invalid/sensitive-pattern-fragment"

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            result = prober._rewrite_url_for_profile(
                url,
                _profile(pattern, replacement),
            )

        assert result == url
        assert "replacement template error" in caplog.text
        assert "sensitive-pattern" not in caplog.text
        assert "sensitive-template" not in caplog.text
        assert "pattern_excerpt=" not in caplog.text
        assert "repl_excerpt=" not in caplog.text
