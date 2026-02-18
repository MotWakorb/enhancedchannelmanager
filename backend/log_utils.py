"""
Logging utilities for safe log output.

Provides a custom LogRecord factory that sanitizes log arguments
to prevent log injection attacks (CWE-117). User-provided values
(channel names, URLs, etc.) could contain newlines or control
characters that forge log entries.

Install once at startup via install_safe_logging().
"""

import logging

_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def _sanitize_value(value):
    """Strip newlines and carriage returns from a value for safe logging."""
    if isinstance(value, str):
        return value.replace('\r\n', '\\r\\n').replace('\r', '\\r').replace('\n', '\\n')
    return value


def _safe_record_factory(*args, **kwargs):
    """LogRecord factory that sanitizes args to prevent log injection."""
    record = _ORIGINAL_FACTORY(*args, **kwargs)
    if record.args:
        if isinstance(record.args, dict):
            record.args = {k: _sanitize_value(v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_sanitize_value(a) for a in record.args)
    return record


def install_safe_logging():
    """
    Install a global LogRecord factory that sanitizes all log arguments.

    Call once during application startup, before any logging occurs.
    This prevents log injection (CWE-117) by escaping newlines and
    control characters in user-provided values that flow into log calls.
    """
    logging.setLogRecordFactory(_safe_record_factory)
