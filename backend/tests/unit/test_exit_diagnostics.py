"""
Unit tests for exit_diagnostics (bd-0gt2i / GH #546).

The hooks must log loudly with the [EXIT-DIAG] prefix and remain
side-effect-free: they delegate to the previously-installed hook (or the
interpreter default) after logging.
"""
import asyncio
import atexit
import logging
import sys
import threading
import types
from unittest.mock import MagicMock

import pytest

import exit_diagnostics as ed


@pytest.fixture
def hook_state():
    """Snapshot and restore all process-level hook state around a test.

    main.py installs the hooks at import time, so tests must not leave the
    module in a different install state than they found it.
    """
    saved = (
        ed._installed,
        ed._prev_sys_excepthook,
        ed._prev_threading_excepthook,
        sys.excepthook,
        threading.excepthook,
    )
    yield ed
    atexit.unregister(ed.log_atexit)
    (
        ed._installed,
        ed._prev_sys_excepthook,
        ed._prev_threading_excepthook,
        sys.excepthook,
        threading.excepthook,
    ) = saved
    if ed._installed:
        atexit.register(ed.log_atexit)


def _make_exception() -> BaseException:
    try:
        raise ValueError("boom from test")
    except ValueError as e:
        return e


class TestHookLogging:
    """Each hook logs a distinctive [EXIT-DIAG] line when invoked directly."""

    def test_log_atexit_logs_exit_diag_marker(self, caplog):
        with caplog.at_level(logging.ERROR, logger="exit_diagnostics"):
            ed.log_atexit()

        assert any(
            "[EXIT-DIAG]" in r.getMessage() and "atexit" in r.getMessage()
            for r in caplog.records
        )
        # Must explain how to read the log (SystemExit vs hard-kill).
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "SystemExit" in combined
        assert "SIGKILL" in combined

    def test_log_uncaught_exception_includes_type_and_traceback(self, caplog):
        exc = _make_exception()
        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            ed.log_uncaught_exception(type(exc), exc, exc.__traceback__)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.CRITICAL
        msg = record.getMessage()
        assert "[EXIT-DIAG]" in msg
        assert "ValueError" in msg
        assert "boom from test" in msg
        assert "Traceback" in msg

    def test_log_thread_exception_includes_thread_name(self, caplog):
        exc = _make_exception()
        args = types.SimpleNamespace(
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
            thread=types.SimpleNamespace(name="worker-7"),
        )
        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            ed.log_thread_exception(args)

        msg = caplog.records[0].getMessage()
        assert "[EXIT-DIAG]" in msg
        assert "worker-7" in msg
        assert "ValueError" in msg
        assert "boom from test" in msg

    def test_log_thread_exception_handles_missing_thread(self, caplog):
        exc = _make_exception()
        args = types.SimpleNamespace(
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
            thread=None,
        )
        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            ed.log_thread_exception(args)

        assert "<unknown>" in caplog.records[0].getMessage()


class TestAsyncioHandler:
    """The loop exception handler logs then delegates to the default handler."""

    def test_logs_exception_and_delegates(self, caplog):
        loop = MagicMock()
        exc = _make_exception()
        context = {"message": "task exception", "exception": exc}

        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            ed.asyncio_exception_handler(loop, context)

        msg = caplog.records[0].getMessage()
        assert "[EXIT-DIAG]" in msg
        assert "ValueError" in msg
        assert "boom from test" in msg
        loop.default_exception_handler.assert_called_once_with(context)

    def test_logs_context_without_exception(self, caplog):
        loop = MagicMock()
        context = {"message": "socket.send() raised"}

        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            ed.asyncio_exception_handler(loop, context)

        msg = caplog.records[0].getMessage()
        assert "[EXIT-DIAG]" in msg
        assert "socket.send() raised" in msg
        loop.default_exception_handler.assert_called_once_with(context)


class TestInstall:
    """install()/uninstall() lifecycle and hook chaining."""

    def test_install_sets_hooks_and_is_idempotent(self, hook_state):
        ed.uninstall()
        assert not ed.is_installed()

        ed.install()
        assert ed.is_installed()
        assert sys.excepthook is ed._sys_excepthook
        assert threading.excepthook is ed._threading_excepthook

        # Second install must not re-wrap (which would double-log).
        prev_sys = ed._prev_sys_excepthook
        ed.install()
        assert sys.excepthook is ed._sys_excepthook
        assert ed._prev_sys_excepthook is prev_sys

    def test_sys_excepthook_logs_and_chains_to_previous(self, hook_state, caplog):
        ed.uninstall()
        previous = MagicMock()
        sys.excepthook = previous
        ed.install()

        exc = _make_exception()
        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            sys.excepthook(type(exc), exc, exc.__traceback__)

        assert any("[EXIT-DIAG]" in r.getMessage() for r in caplog.records)
        previous.assert_called_once_with(type(exc), exc, exc.__traceback__)

    def test_threading_excepthook_logs_and_chains_to_previous(self, hook_state, caplog):
        ed.uninstall()
        previous = MagicMock()
        threading.excepthook = previous
        ed.install()

        exc = _make_exception()
        args = types.SimpleNamespace(
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
            thread=None,
        )
        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            threading.excepthook(args)

        assert any("[EXIT-DIAG]" in r.getMessage() for r in caplog.records)
        previous.assert_called_once_with(args)

    def test_uninstall_restores_previous_hooks(self, hook_state):
        ed.uninstall()
        sentinel_sys = MagicMock()
        sentinel_threading = MagicMock()
        sys.excepthook = sentinel_sys
        threading.excepthook = sentinel_threading

        ed.install()
        ed.uninstall()

        assert sys.excepthook is sentinel_sys
        assert threading.excepthook is sentinel_threading
        assert not ed.is_installed()

    def test_main_module_installs_hooks_at_import(self):
        import main  # noqa: F401 — hooks installed at import time

        assert ed.is_installed()


class TestInstallLoopHandler:
    """Loop handler installation on real event loops."""

    def test_installs_handler_on_bare_loop(self):
        loop = asyncio.new_event_loop()
        try:
            assert loop.get_exception_handler() is None
            ed.install_loop_handler(loop)
            assert loop.get_exception_handler() is ed.asyncio_exception_handler
        finally:
            loop.close()

    def test_preserves_existing_custom_handler(self, caplog):
        loop = asyncio.new_event_loop()
        try:
            previous = MagicMock()
            loop.set_exception_handler(previous)
            ed.install_loop_handler(loop)

            handler = loop.get_exception_handler()
            assert handler is not previous  # wrapped

            exc = _make_exception()
            context = {"message": "task exception", "exception": exc}
            with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
                handler(loop, context)

            assert any("[EXIT-DIAG]" in r.getMessage() for r in caplog.records)
            previous.assert_called_once_with(loop, context)
        finally:
            loop.close()
