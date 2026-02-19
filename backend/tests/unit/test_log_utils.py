"""Tests for log_utils — log injection sanitizer."""

import logging

from log_utils import _sanitize_value, _safe_record_factory, install_safe_logging


class TestSanitizeValue:
    def test_strips_newlines(self):
        assert _sanitize_value("line1\nline2") == "line1\\nline2"

    def test_strips_carriage_returns(self):
        assert _sanitize_value("line1\rline2") == "line1\\rline2"

    def test_strips_crlf(self):
        assert _sanitize_value("line1\r\nline2") == "line1\\r\\nline2"

    def test_passes_non_strings(self):
        assert _sanitize_value(42) == 42
        assert _sanitize_value(3.14) == 3.14
        assert _sanitize_value(None) is None

    def test_clean_string_unchanged(self):
        assert _sanitize_value("normal text") == "normal text"

    def test_multiple_newlines(self):
        assert _sanitize_value("a\nb\nc") == "a\\nb\\nc"


class TestSafeRecordFactory:
    """Test the factory function directly."""

    def _make_record(self, msg, args):
        return _safe_record_factory(
            "test", logging.INFO, __file__, 0, msg, args, None,
        )

    def test_sanitizes_tuple_args(self):
        record = self._make_record("Channel %s has %d streams", ("Evil\nChannel", 5))
        assert record.getMessage() == "Channel Evil\\nChannel has 5 streams"

    def test_sanitizes_crlf(self):
        record = self._make_record("Name: %s", ("Bad\r\nName",))
        assert record.getMessage() == "Name: Bad\\r\\nName"

    def test_no_args_unchanged(self):
        record = self._make_record("Simple message", None)
        assert record.getMessage() == "Simple message"

    def test_non_string_args_passed_through(self):
        record = self._make_record("Count: %d, Ratio: %.1f", (42, 3.14))
        assert record.getMessage() == "Count: 42, Ratio: 3.1"

    def test_mixed_args(self):
        record = self._make_record(
            "[PROBE] %s streams=%d url=%s",
            ("Injected\nLine", 3, "http://evil.com/path\nnewline"),
        )
        msg = record.getMessage()
        assert "\\n" in msg
        assert "\n" not in msg


class TestInstallSafeLogging:
    def setup_method(self):
        self._original = logging.getLogRecordFactory()

    def teardown_method(self):
        logging.setLogRecordFactory(self._original)

    def test_installs_factory(self):
        install_safe_logging()
        assert logging.getLogRecordFactory() is _safe_record_factory

    def test_logger_uses_factory(self):
        install_safe_logging()
        test_logger = logging.getLogger("test.install")
        # makeRecord is what the logging module calls internally
        record = test_logger.makeRecord(
            "test", logging.INFO, __file__, 0,
            "Channel %s", ("Evil\nName",), None,
        )
        assert record.getMessage() == "Channel Evil\\nName"
