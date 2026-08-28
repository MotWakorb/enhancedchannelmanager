"""Tests for safe, persistent, and debug-bundle logging utilities."""

import builtins
import errno
import fcntl
import json
import logging
import multiprocessing
import os
import stat
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest

import log_utils
import observability


def _multiprocess_log_writer(
    config_dir: str,
    worker_id: int,
    count: int,
    max_bytes: int,
) -> None:
    """Independent process target used by the interprocess rotation proof."""
    handler = log_utils.InterProcessRotatingJsonHandler(
        Path(config_dir) / "logs" / "ecm.log",
        max_bytes=max_bytes,
        backup_count=9,
    )
    handler.setFormatter(observability.JsonFormatter())
    handler.addFilter(observability._TraceIdFilter())
    for index in range(count):
        record = logging.LogRecord(
            f"worker.{worker_id}",
            logging.INFO,
            __file__,
            0,
            "worker=%s index=%s",
            (worker_id, index),
            None,
        )
        handler.handle(record)
    handler.close()


@pytest.fixture
def isolated_file_logging():
    """Keep root handlers and one-shot diagnostics isolated per test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_filters = root.filters[:]
    saved_level = root.level
    log_utils._reset_file_logging_for_tests()
    log_utils._reset_sensitive_values_for_tests()
    yield
    log_utils._reset_file_logging_for_tests()
    log_utils._reset_sensitive_values_for_tests()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for log_filter in root.filters[:]:
        root.removeFilter(log_filter)
    for handler in saved_handlers:
        root.addHandler(handler)
    for log_filter in saved_filters:
        root.addFilter(log_filter)
    root.setLevel(saved_level)


class TestSanitizeValue:
    def test_strips_newlines(self):
        assert log_utils._sanitize_value("line1\nline2") == "line1\\nline2"

    def test_strips_carriage_returns(self):
        assert log_utils._sanitize_value("line1\rline2") == "line1\\rline2"

    def test_strips_crlf(self):
        assert log_utils._sanitize_value("line1\r\nline2") == "line1\\r\\nline2"

    def test_passes_non_strings(self):
        assert log_utils._sanitize_value(42) == 42
        assert log_utils._sanitize_value(3.14) == 3.14
        assert log_utils._sanitize_value(None) is None

    def test_clean_string_unchanged(self):
        assert log_utils._sanitize_value("normal text") == "normal text"

    def test_multiple_newlines(self):
        assert log_utils._sanitize_value("a\nb\nc") == "a\\nb\\nc"


class TestSafeRecordFactory:
    """Test the factory function directly."""

    def _make_record(self, msg, args):
        return log_utils._safe_record_factory(
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
        log_utils.install_safe_logging()
        assert logging.getLogRecordFactory() is log_utils._safe_record_factory

    def test_logger_uses_factory(self):
        log_utils.install_safe_logging()
        test_logger = logging.getLogger("test.install")
        # makeRecord is what the logging module calls internally
        record = test_logger.makeRecord(
            "test", logging.INFO, __file__, 0,
            "Channel %s", ("Evil\nName",), None,
        )
        assert record.getMessage() == "Channel Evil\\nName"


class TestRingBufferAccounting:
    def test_saturation_and_overwrites_are_exact(self):
        handler = log_utils.RingBufferHandler(capacity=2)
        handler.setFormatter(logging.Formatter("%(message)s"))

        handler.handle(logging.LogRecord("ring", logging.INFO, __file__, 0, "one", (), None))
        first = handler.get_snapshot()
        assert first.lines == ("one",)
        assert first.saturated is False
        assert first.overwrite_count == 0

        handler.handle(logging.LogRecord("ring", logging.INFO, __file__, 0, "two", (), None))
        full = handler.get_snapshot()
        assert full.lines == ("one", "two")
        assert full.saturated is True
        assert full.overwrite_count == 0

        handler.handle(logging.LogRecord("ring", logging.INFO, __file__, 0, "three", (), None))
        overwritten = handler.get_snapshot()
        assert overwritten.lines == ("two", "three")
        assert overwritten.saturated is True
        assert overwritten.overwrite_count == 1


class TestInterProcessRotatingJsonHandler:
    @staticmethod
    def _handler(config_dir: Path, *, max_bytes=320, backup_count=9):
        handler = log_utils.InterProcessRotatingJsonHandler(
            config_dir / "logs" / "ecm.log",
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
        handler.setFormatter(observability.JsonFormatter())
        handler.addFilter(observability._TraceIdFilter())
        return handler

    @staticmethod
    def _emit(handler, message: str) -> None:
        handler.handle(
            logging.LogRecord(
                "rotation.test", logging.INFO, __file__, 0, message, (), None
            )
        )

    def test_rotates_at_small_bound_and_snapshot_is_oldest_first(self, tmp_path):
        handler = self._handler(tmp_path, max_bytes=300)
        expected = [f"sequence-{index}-" + ("x" * 35) for index in range(12)]
        for message in expected:
            self._emit(handler, message)

        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=9)
        messages = [
            json.loads(line)["msg"]
            for source in snapshot.files
            for line in source.data.decode("utf-8").splitlines()
        ]

        assert len(snapshot.files) > 1
        assert [source.name for source in snapshot.files] == sorted(
            [source.name for source in snapshot.files],
            key=lambda name: (0, -int(name.rsplit(".", 1)[1]))
            if name != "ecm.log" else (1, 0),
        )
        assert messages == expected
        assert len(messages) == len(set(messages))
        handler.close()

    def test_saturation_starts_only_after_rotation_discards_a_file(self, tmp_path):
        handler = self._handler(tmp_path, max_bytes=160, backup_count=2)

        for message in ("one", "two", "three"):
            self._emit(handler, message)

        full = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
        assert [source.name for source in full.files] == [
            "ecm.log.2",
            "ecm.log.1",
            "ecm.log",
        ]
        assert full.rotation_saturated is False

        self._emit(handler, "four")

        overwritten = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
        assert overwritten.rotation_saturated is True
        handler.close()

    def test_snapshot_keeps_newest_bytes_and_releases_lock_before_reading(
        self, tmp_path
    ):
        handler = self._handler(tmp_path, max_bytes=320, backup_count=2)
        for message in ("oldest", "middle", "newest"):
            self._emit(handler, message)

        original_read = os.read
        lock_was_available: list[bool] = []

        def read_with_lock_probe(descriptor, size):
            probe = os.open(handler.lock_path, os.O_RDWR | os.O_CLOEXEC)
            try:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock_was_available.append(False)
                else:
                    lock_was_available.append(True)
                    fcntl.flock(probe, fcntl.LOCK_UN)
            finally:
                os.close(probe)
            return original_read(descriptor, size)

        with patch("log_utils.os.read", side_effect=read_with_lock_probe):
            snapshot = log_utils.snapshot_persistent_logs(
                tmp_path,
                backup_count=2,
                max_total_bytes=200,
            )

        messages = [
            json.loads(line)["msg"]
            for source in snapshot.files
            for line in source.data.decode().splitlines()
        ]
        assert snapshot.byte_limit_reached is True
        assert messages == ["newest"]
        assert lock_was_available and all(lock_was_available)
        handler.close()

    def test_writer_progresses_while_snapshot_read_is_stalled(self, tmp_path):
        handler = self._handler(tmp_path, max_bytes=1024, backup_count=2)
        self._emit(handler, "before-snapshot")
        read_started = threading.Event()
        release_read = threading.Event()
        original_read = os.read

        def stalled_read(descriptor, size):
            read_started.set()
            assert release_read.wait(timeout=5)
            return original_read(descriptor, size)

        snapshot_result = {}

        def take_snapshot():
            snapshot_result["value"] = log_utils.snapshot_persistent_logs(
                tmp_path, backup_count=2
            )

        with patch("log_utils.os.read", side_effect=stalled_read):
            snapshot_thread = threading.Thread(target=take_snapshot)
            snapshot_thread.start()
            assert read_started.wait(timeout=5)

            writer_thread = threading.Thread(
                target=lambda: self._emit(handler, "writer-progressed")
            )
            writer_thread.start()
            writer_thread.join(timeout=2)
            assert not writer_thread.is_alive(), "snapshot held the writer flock during I/O"

            release_read.set()
            snapshot_thread.join(timeout=5)

        assert not snapshot_thread.is_alive()
        assert snapshot_result["value"].incomplete is False
        handler.close()

    def test_install_is_idempotent_and_uses_private_permissions(
        self, tmp_path, isolated_file_logging
    ):
        first = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )
        second = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )

        assert first is second
        installed = [
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "_ecm_persistent_json_handler", False)
        ]
        assert installed == [first]
        assert stat.S_IMODE((tmp_path / "logs").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "logs" / "ecm.log").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "logs" / ".ecm.log.lock").stat().st_mode) == 0o600

        policy = log_utils.get_persistent_log_policy()
        assert policy is not None
        assert policy.max_bytes == 1024
        assert policy.backup_count == 2

    def test_restart_reopens_existing_set_without_losing_pre_restart_lines(
        self, tmp_path, isolated_file_logging
    ):
        first = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )
        self._emit(first, "before-simulated-restart")
        log_utils._reset_file_logging_for_tests()

        second = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )
        self._emit(second, "after-simulated-restart")
        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
        messages = [
            json.loads(line)["msg"]
            for source in snapshot.files
            for line in source.data.decode().splitlines()
        ]

        assert messages == ["before-simulated-restart", "after-simulated-restart"]

    def test_restart_with_lower_backup_count_prunes_excess_files(
        self, tmp_path
    ):
        first = self._handler(tmp_path, max_bytes=160, backup_count=4)
        for message in ("one", "two", "three"):
            self._emit(first, message)
        first.close()
        assert (tmp_path / "logs" / "ecm.log.2").exists()

        second = self._handler(tmp_path, max_bytes=160, backup_count=1)
        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=1)

        assert not (tmp_path / "logs" / "ecm.log.2").exists()
        assert [source.name for source in snapshot.files] == ["ecm.log.1", "ecm.log"]
        assert snapshot.rotation_saturated is True
        second.close()

    def test_concurrent_processes_rotate_without_duplicate_or_lost_records(self, tmp_path):
        context = multiprocessing.get_context("spawn")
        process_count = 4
        records_per_process = 12
        processes = [
            context.Process(
                target=_multiprocess_log_writer,
                args=(str(tmp_path), worker, records_per_process, 1024),
            )
            for worker in range(process_count)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0

        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=9)
        observed = {
            json.loads(line)["msg"]
            for source in snapshot.files
            for line in source.data.decode().splitlines()
        }
        expected = {
            f"worker={worker} index={index}"
            for worker in range(process_count)
            for index in range(records_per_process)
        }
        line_count = sum(
            len(source.data.decode().splitlines()) for source in snapshot.files
        )

        assert snapshot.incomplete is False
        assert observed == expected
        assert line_count == len(expected)

    def test_unwritable_install_degrades_without_adding_a_handler(
        self, tmp_path, isolated_file_logging
    ):
        with patch("log_utils.os.mkdir", side_effect=PermissionError), patch(
            "log_utils._direct_stderr_diagnostic"
        ) as diagnostic:
            handler = log_utils.install_persistent_json_logging(
                tmp_path, max_bytes=1024, backup_count=2
            )

        assert handler is None
        assert not any(
            getattr(item, "_ecm_persistent_json_handler", False)
            for item in logging.getLogger().handlers
        )
        diagnostic.assert_called_once_with()

    def test_enospc_disables_detaches_and_diagnoses_once_without_record_data(
        self, tmp_path, isolated_file_logging
    ):
        handler = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )
        record = logging.LogRecord(
            "disk.full",
            logging.ERROR,
            __file__,
            0,
            "credential=%s",
            ("must-not-reach-diagnostic",),
            None,
        )

        with patch.object(
            handler,
            "_write_all_locked",
            side_effect=OSError(errno.ENOSPC, "synthetic full disk"),
        ), patch("log_utils._direct_stderr_diagnostic") as diagnostic:
            handler.handle(record)
            handler.handle(record)

        assert handler.disabled is True
        assert handler not in logging.getLogger().handlers
        diagnostic.assert_called_once_with()

    def test_rollover_failure_disables_and_detaches_without_retrying(
        self, tmp_path, isolated_file_logging
    ):
        handler = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=160, backup_count=2
        )
        self._emit(handler, "first record fills the deliberately tiny active file")

        with patch("log_utils.os.replace", side_effect=PermissionError), patch(
            "log_utils._direct_stderr_diagnostic"
        ) as diagnostic:
            self._emit(handler, "second record requires rollover")
            self._emit(handler, "disabled handler must not retry")

        assert handler.disabled is True
        assert handler not in logging.getLogger().handlers
        diagnostic.assert_called_once_with()

    def test_snapshot_exposes_partial_read_without_paths_or_error_text(self, tmp_path):
        handler = self._handler(tmp_path, max_bytes=1024)
        self._emit(handler, "fixed-size-read")

        with patch("log_utils.os.read", return_value=b""):
            snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=9)

        assert snapshot.incomplete is True
        assert snapshot.reason == "source_read_incomplete"
        assert "/" not in snapshot.reason
        assert "ecm.log" not in snapshot.reason
        assert snapshot.files[0].complete is False
        handler.close()

    def test_registered_retired_and_encoded_credentials_are_redacted_before_write(
        self, tmp_path, isolated_file_logging
    ):
        retired = "retired-credential-value"
        short = "xy"
        escaped = 'json-"quoted"-credential'
        non_url = "non-url-provider-secret"
        log_utils.register_sensitive_values(retired, short, escaped, non_url)

        handler = self._handler(tmp_path, max_bytes=1024 * 1024, backup_count=2)
        self._emit(
            handler,
            "retired=%s short=%s encoded=%s escaped=%s non-url text %s"
            % (
                retired,
                short,
                quote(retired, safe=""),
                json.dumps(escaped)[1:-1],
                non_url,
            ),
        )
        # Registration is additive: rotating to a successor must not make the
        # retired value eligible to reappear later in the same process.
        log_utils.register_sensitive_values("successor-credential-value")
        self._emit(handler, f"retired credential repeated: {retired}")

        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
        persisted = b"".join(source.data for source in snapshot.files)
        for unsafe in (
            retired.encode(),
            short.encode(),
            quote(retired, safe="").encode(),
            json.dumps(escaped)[1:-1].encode(),
            non_url.encode(),
        ):
            assert unsafe not in persisted
        assert b"***REDACTED***" in persisted
        handler.close()

    def test_object_registration_does_not_import_the_backup_router(
        self, isolated_file_logging, monkeypatch
    ):
        real_import = builtins.__import__

        def import_without_backup(
            name, globals=None, locals=None, fromlist=(), level=0
        ):
            if name == "routers.backup":
                raise AssertionError("logging credential registration imported a router")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", import_without_backup)

        log_utils.register_sensitive_values_from_object(
            {
                "password": "provider-secret-value",
                "username": "provider-identity-value",
            }
        )

        registered = log_utils.get_registered_sensitive_value_forms()
        assert "provider-secret-value" in registered
        assert "provider-identity-value" in registered

    def test_unknown_credential_shapes_use_the_shared_pattern_scrubber(
        self, tmp_path, isolated_file_logging
    ):
        handler = self._handler(tmp_path, max_bytes=1024 * 1024, backup_count=2)
        self._emit(
            handler,
            "Authorization: Bearer unknown-token-123 password=unknown-pass-456",
        )

        persisted = b"".join(
            source.data
            for source in log_utils.snapshot_persistent_logs(
                tmp_path, backup_count=2
            ).files
        )
        assert b"unknown-token-123" not in persisted
        assert b"unknown-pass-456" not in persisted
        assert b"***REDACTED***" in persisted
        handler.close()

    def test_oversized_record_becomes_bounded_valid_ndjson_marker(self, tmp_path):
        max_bytes = 512
        handler = self._handler(tmp_path, max_bytes=max_bytes, backup_count=2)
        self._emit(handler, "oversized-" + ("x" * 10_000))

        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
        assert snapshot.files
        for source in snapshot.files:
            assert len(source.data) <= max_bytes
            for line in source.data.splitlines():
                payload = json.loads(line)
                assert payload["event"] == "persistent_log_record_dropped"
                assert payload["original_bytes"] > max_bytes
                assert len(payload["sha256"]) == 64
        handler.close()

    def test_every_generation_stays_bounded_after_repeated_oversized_records(
        self, tmp_path
    ):
        max_bytes = 512
        handler = self._handler(tmp_path, max_bytes=max_bytes, backup_count=2)
        for index in range(8):
            self._emit(handler, f"record-{index}-" + ("z" * 5000))

        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
        assert snapshot.files
        assert all(len(source.data) <= max_bytes for source in snapshot.files)
        assert all(
            json.loads(line)["event"] == "persistent_log_record_dropped"
            for source in snapshot.files
            for line in source.data.splitlines()
        )
        handler.close()

    def test_unmarked_historical_epoch_is_not_returned(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(mode=0o700)
        retired = b'{"msg":"retired-only-credential"}\n'
        (logs_dir / "ecm.log").write_bytes(retired)

        snapshot = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)

        assert snapshot.files == ()
        assert snapshot.incomplete is True
        assert snapshot.reason == "unsafe_log_epoch"

    def test_secure_handler_discards_unmarked_epoch_before_new_writes(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(mode=0o700)
        retired = b'{"msg":"retired-only-credential"}\n'
        (logs_dir / "ecm.log").write_bytes(retired)

        handler = self._handler(tmp_path, max_bytes=1024, backup_count=2)
        self._emit(handler, "new-safe-epoch")
        persisted = b"".join(
            source.data
            for source in log_utils.snapshot_persistent_logs(
                tmp_path, backup_count=2
            ).files
        )

        assert retired.rstrip() not in persisted
        assert b"new-safe-epoch" in persisted
        handler.close()

    def test_symlinked_log_directory_is_refused(
        self, tmp_path, isolated_file_logging
    ):
        target = tmp_path / "attacker-dir"
        target.mkdir()
        (tmp_path / "logs").symlink_to(target, target_is_directory=True)

        handler = log_utils.install_persistent_json_logging(
            tmp_path, max_bytes=1024, backup_count=2
        )

        assert handler is None
        assert not (target / "ecm.log").exists()

    def test_fifo_active_log_is_refused_without_blocking(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(mode=0o700)
        os.mkfifo(logs_dir / "ecm.log", mode=0o600)

        started = time.monotonic()
        with pytest.raises(OSError):
            self._handler(tmp_path, max_bytes=1024, backup_count=2)
        assert time.monotonic() - started < 2

    def test_hardlinked_active_log_is_refused(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(mode=0o700)
        outside = tmp_path / "outside.log"
        outside.write_text("do not overwrite")
        os.link(outside, logs_dir / "ecm.log")

        with pytest.raises(OSError):
            self._handler(tmp_path, max_bytes=1024, backup_count=2)
        assert outside.read_text() == "do not overwrite"

    def test_replaced_lock_identity_disables_handler_before_next_write(self, tmp_path):
        handler = self._handler(tmp_path, max_bytes=1024, backup_count=2)
        self._emit(handler, "before-lock-swap")
        original_lock = handler.lock_path.with_name("old-lock")
        os.replace(handler.lock_path, original_lock)
        handler.lock_path.write_text("")

        self._emit(handler, "must-not-write-after-lock-swap")

        assert handler.disabled is True
        persisted = b"".join(
            source.data
            for source in log_utils.snapshot_persistent_logs(
                tmp_path, backup_count=2
            ).files
        )
        assert b"must-not-write-after-lock-swap" not in persisted
