"""
Logging utilities for safe log output.

Provides a custom LogRecord factory that sanitizes log arguments
to prevent log injection attacks (CWE-117). User-provided values
(channel names, URLs, etc.) could contain newlines or control
characters that forge log entries.

Install once at startup via install_safe_logging().
"""

import collections
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
import stat
import threading
from pathlib import Path
from urllib.parse import quote, quote_plus

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


# =========================================================================
# Ring buffer handler — captures recent log lines for debug bundles
# =========================================================================

class RingBufferHandler(logging.Handler):
    """Logging handler that keeps the last *capacity* formatted log lines."""

    def __init__(self, capacity: int = 10000):
        super().__init__()
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._buffer: collections.deque = collections.deque(maxlen=capacity)
        self._overwrite_count = 0

    def emit(self, record):
        try:
            if len(self._buffer) == self._buffer.maxlen:
                self._overwrite_count += 1
            self._buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def get_lines(self) -> list[str]:
        return list(self.get_snapshot().lines)

    def get_snapshot(self) -> "RingBufferSnapshot":
        """Return lines and exact capacity accounting from one locked instant."""
        self.acquire()
        try:
            return RingBufferSnapshot(
                lines=tuple(self._buffer),
                capacity=int(self._buffer.maxlen),
                saturated=len(self._buffer) == self._buffer.maxlen,
                overwrite_count=self._overwrite_count,
            )
        finally:
            self.release()


@dataclass(frozen=True)
class RingBufferSnapshot:
    lines: tuple[str, ...]
    capacity: int
    saturated: bool
    overwrite_count: int


_ring_handler: RingBufferHandler | None = None


def install_ring_buffer(capacity: int = 10000):
    """Install a ring-buffer handler on the root logger.

    Call once at startup, after :func:`install_safe_logging`.
    """
    global _ring_handler
    _ring_handler = RingBufferHandler(capacity)
    _ring_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.getLogger().addHandler(_ring_handler)


def get_recent_logs() -> list[str]:
    """Return the most recent log lines captured by the ring buffer."""
    if _ring_handler is None:
        return []
    return _ring_handler.get_lines()


def get_ring_buffer_snapshot() -> RingBufferSnapshot:
    """Return the ring contents and exact overwrite state atomically."""
    if _ring_handler is None:
        return RingBufferSnapshot((), 0, False, 0)
    return _ring_handler.get_snapshot()


# =========================================================================
# Interprocess-safe rotating JSON file logging
# =========================================================================

_PERSISTENT_LOG_FILENAME = "ecm.log"
_PERSISTENT_LOG_LOCK_FILENAME = ".ecm.log.lock"
_PERSISTENT_LOG_SATURATION_FILENAME = ".ecm.log.saturated"
_PERSISTENT_LOG_EPOCH_FILENAME = ".ecm.log.scrubbed-v1"
_PERSISTENT_LOG_EPOCH_CONTENT = b"ecm-scrubbed-v1\n"
_PERSISTENT_LOG_DIAGNOSTIC = (
    b"ECM persistent logging disabled after a storage error.\n"
)
_persistent_handler: "InterProcessRotatingJsonHandler | None" = None
_diagnostic_lock = threading.Lock()
_file_diagnostic_emitted = False
_sensitive_values_lock = threading.Lock()
_sensitive_value_forms: set[str] = set()


def _sensitive_forms(value: str) -> set[str]:
    return {
        value,
        quote(value, safe=""),
        quote_plus(value),
        json.dumps(value)[1:-1],
        value.replace('"', '""'),
        value.replace("'", "''"),
    }


def register_sensitive_values(*values: str) -> None:
    """Add credential spellings to the process-lifetime persistence scrubber.

    Values are never removed during normal operation. A rotated credential can
    still occur in an exception or delayed task, so forgetting it would make a
    later log write unsafe.
    """
    forms: set[str] = set()
    for value in values:
        if isinstance(value, str) and value and value != "***REDACTED***":
            forms.update(form for form in _sensitive_forms(value) if form)
    if forms:
        with _sensitive_values_lock:
            _sensitive_value_forms.update(forms)


def register_sensitive_values_from_object(value) -> None:
    """Harvest values with the backup artifact's canonical credential rules."""
    from routers.backup import _collect_credential_values

    secrets, identities = _collect_credential_values(value)
    register_sensitive_values(*(secrets | identities))


def get_registered_sensitive_value_forms() -> tuple[str, ...]:
    """Return an immutable snapshot of process-lifetime sensitive spellings."""
    with _sensitive_values_lock:
        return tuple(sorted(_sensitive_value_forms, key=len, reverse=True))


def _redact_persistent_string(value: str) -> str:
    from cloud_storage.upload_security import redact_secrets

    redacted = redact_secrets(value)
    for form in get_registered_sensitive_value_forms():
        redacted = redacted.replace(form, "***REDACTED***")
    return redacted


def _redact_persistent_value(value):
    if isinstance(value, str):
        return _redact_persistent_string(value)
    if isinstance(value, list):
        return [_redact_persistent_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _redact_persistent_string(key) if isinstance(key, str) else key:
            _redact_persistent_value(item)
            for key, item in value.items()
        }
    return value


def _safe_persistent_record(rendered: str, max_bytes: int) -> bytes:
    """Scrub one formatted JSON record and enforce the generation byte bound."""
    try:
        payload = json.loads(rendered)
        scrubbed = json.dumps(
            _redact_persistent_value(payload),
            default=str,
            separators=(",", ":"),
        ).encode("utf-8", errors="backslashreplace") + b"\n"
    except Exception:
        scrubbed = b'{"event":"persistent_log_record_dropped","reason":"scrub_failed"}\n'

    if len(scrubbed) <= max_bytes:
        return scrubbed
    marker = json.dumps(
        {
            "event": "persistent_log_record_dropped",
            "original_bytes": len(scrubbed),
            "sha256": hashlib.sha256(scrubbed).hexdigest(),
        },
        separators=(",", ":"),
    ).encode("ascii") + b"\n"
    return marker if len(marker) <= max_bytes else b""


def _open_secure_log_directory(path: Path, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError:
            pass
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        opened = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (named.st_dev, named.st_ino):
            raise OSError("persistent log directory identity changed")
        os.fchmod(descriptor, 0o700)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_private_regular(descriptor: int, *, label: str) -> os.stat_result:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise OSError(f"persistent log {label} is not a private regular file")
    os.fchmod(descriptor, 0o600)
    return opened


def _epoch_marker_is_valid(directory_fd: int) -> bool:
    try:
        descriptor = os.open(
            _PERSISTENT_LOG_EPOCH_FILENAME,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return False
    try:
        marker_stat = _validate_private_regular(descriptor, label="epoch marker")
        if marker_stat.st_size != len(_PERSISTENT_LOG_EPOCH_CONTENT):
            return False
        return os.pread(
            descriptor, len(_PERSISTENT_LOG_EPOCH_CONTENT) + 1, 0
        ) == (
            _PERSISTENT_LOG_EPOCH_CONTENT
        )
    finally:
        os.close(descriptor)


def _rotating_log_names(directory_fd: int) -> list[str]:
    prefix = f"{_PERSISTENT_LOG_FILENAME}."
    names = []
    for name in os.listdir(directory_fd):
        if name == _PERSISTENT_LOG_FILENAME:
            names.append(name)
            continue
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            names.append(name)
    return names


@dataclass(frozen=True)
class PersistentLogSource:
    """One fixed-size file read from the rotating set."""

    name: str
    data: bytes
    expected_bytes: int
    complete: bool


@dataclass(frozen=True)
class PersistentLogPolicy:
    """Rotation policy applied by this process's live handler."""

    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class PersistentLogSnapshot:
    """Oldest-to-newest rotating-set snapshot with bounded error metadata."""

    files: tuple[PersistentLogSource, ...]
    incomplete: bool
    reason: str | None
    handler_degraded: bool
    rotation_saturated: bool
    byte_limit_reached: bool = False


@dataclass(frozen=True)
class _OpenedPersistentLogSource:
    name: str
    descriptor: int
    size: int


def _direct_stderr_diagnostic() -> None:
    """Emit one fixed process-local diagnostic without entering logging."""
    global _file_diagnostic_emitted
    with _diagnostic_lock:
        if _file_diagnostic_emitted:
            return
        _file_diagnostic_emitted = True
    try:
        os.write(2, _PERSISTENT_LOG_DIAGNOSTIC)
    except OSError:
        pass


class InterProcessRotatingJsonHandler(logging.Handler):
    """Size-rotating handler serialized across ECM's HTTP/HTTPS processes.

    A private flock file covers active-file validation, rollover, and append as
    one critical section. Each process keeps an active descriptor for the fast
    path, but compares its inode with the path after taking the lock so a peer's
    rollover is observed before the next write.
    """

    _ecm_persistent_json_handler = True

    def __init__(self, filename: Path, *, max_bytes: int, backup_count: int):
        super().__init__()
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if backup_count < 1:
            raise ValueError("backup_count must be positive")
        self.base_path = Path(filename)
        self.lock_path = self.base_path.with_name(_PERSISTENT_LOG_LOCK_FILENAME)
        self.saturation_path = self.base_path.with_name(
            _PERSISTENT_LOG_SATURATION_FILENAME
        )
        self.max_bytes = int(max_bytes)
        self.backup_count = int(backup_count)
        self.disabled = False
        self._directory_fd: int | None = None
        self._lock_fd: int | None = None
        self._stream_fd: int | None = None
        try:
            self._directory_fd = _open_secure_log_directory(
                self.base_path.parent, create=True
            )
            self._lock_fd = os.open(
                self.lock_path.name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_CLOEXEC
                | os.O_NOFOLLOW
                | os.O_NONBLOCK,
                0o600,
                dir_fd=self._directory_fd,
            )
            _validate_private_regular(self._lock_fd, label="lock")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                self._validate_lock_identity_locked()
                self._ensure_safe_epoch_locked()
                self._open_stream_locked()
                self._prune_excess_backups_locked()
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except Exception:
            self._close_descriptors()
            raise

    def _open_stream_locked(self) -> None:
        self._close_stream()
        if self._directory_fd is None:
            raise OSError("persistent log directory is unavailable")
        descriptor = os.open(
            self.base_path.name,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=self._directory_fd,
        )
        try:
            _validate_private_regular(descriptor, label="active file")
        except Exception:
            os.close(descriptor)
            raise
        self._stream_fd = descriptor

    def _close_stream(self) -> None:
        if self._stream_fd is None:
            return
        try:
            os.close(self._stream_fd)
        except OSError:
            pass
        self._stream_fd = None

    def _close_descriptors(self) -> None:
        self._close_stream()
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
        if self._directory_fd is not None:
            try:
                os.close(self._directory_fd)
            except OSError:
                pass
            self._directory_fd = None

    def _validate_lock_identity_locked(self) -> None:
        if self._lock_fd is None or self._directory_fd is None:
            raise OSError("persistent log lock is unavailable")
        named = os.stat(
            self.lock_path.name,
            dir_fd=self._directory_fd,
            follow_symlinks=False,
        )
        opened = _validate_private_regular(self._lock_fd, label="lock")
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("persistent log lock identity changed")

    def _ensure_safe_epoch_locked(self) -> None:
        if self._directory_fd is None:
            raise OSError("persistent log directory is unavailable")
        if _epoch_marker_is_valid(self._directory_fd):
            return

        for name in _rotating_log_names(self._directory_fd):
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._directory_fd,
            )
            try:
                _validate_private_regular(descriptor, label="pre-scrub epoch source")
            finally:
                os.close(descriptor)
            os.unlink(name, dir_fd=self._directory_fd)
        try:
            os.unlink(_PERSISTENT_LOG_SATURATION_FILENAME, dir_fd=self._directory_fd)
        except FileNotFoundError:
            pass

        marker_fd = os.open(
            _PERSISTENT_LOG_EPOCH_FILENAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_TRUNC
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=self._directory_fd,
        )
        try:
            _validate_private_regular(marker_fd, label="epoch marker")
            os.write(marker_fd, _PERSISTENT_LOG_EPOCH_CONTENT)
            os.fsync(marker_fd)
        finally:
            os.close(marker_fd)

    def _stream_matches_active_locked(self) -> bool:
        if self._stream_fd is None or self._directory_fd is None:
            return False
        try:
            active = os.stat(
                self.base_path.name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            opened = _validate_private_regular(
                self._stream_fd, label="active file"
            )
        except OSError:
            return False
        return (
            stat.S_ISREG(active.st_mode)
            and active.st_nlink == 1
            and (active.st_dev, active.st_ino) == (opened.st_dev, opened.st_ino)
        )

    def _ensure_active_stream_locked(self) -> None:
        if not self._stream_matches_active_locked():
            self._open_stream_locked()

    def _mark_saturated_locked(self) -> None:
        if self._directory_fd is None:
            raise OSError("persistent log directory is unavailable")
        marker_fd = os.open(
            self.saturation_path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=self._directory_fd,
        )
        try:
            _validate_private_regular(marker_fd, label="saturation marker")
        finally:
            os.close(marker_fd)

    def _prune_excess_backups_locked(self) -> None:
        if self._directory_fd is None:
            raise OSError("persistent log directory is unavailable")
        prefix = f"{self.base_path.name}."
        discarded = False
        for name in os.listdir(self._directory_fd):
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            if not suffix.isdigit() or str(int(suffix)) != suffix:
                continue
            if int(suffix) <= self.backup_count:
                continue
            source_stat = os.stat(
                name, dir_fd=self._directory_fd, follow_symlinks=False
            )
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                raise OSError("persistent log backup is not a private regular file")
            os.unlink(name, dir_fd=self._directory_fd)
            discarded = True
        if discarded:
            self._mark_saturated_locked()

    def _rollover_locked(self) -> None:
        if self._directory_fd is None:
            raise OSError("persistent log directory is unavailable")
        self._close_stream()
        oldest = f"{self.base_path.name}.{self.backup_count}"
        discarded = False
        try:
            oldest_stat = os.stat(
                oldest, dir_fd=self._directory_fd, follow_symlinks=False
            )
            if not stat.S_ISREG(oldest_stat.st_mode) or oldest_stat.st_nlink != 1:
                raise OSError("persistent log backup is not a private regular file")
            discarded = True
            os.unlink(oldest, dir_fd=self._directory_fd)
        except FileNotFoundError:
            pass

        if discarded:
            self._mark_saturated_locked()

        for index in range(self.backup_count - 1, 0, -1):
            source = f"{self.base_path.name}.{index}"
            target = f"{self.base_path.name}.{index + 1}"
            try:
                source_stat = os.stat(
                    source, dir_fd=self._directory_fd, follow_symlinks=False
                )
                if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                    raise OSError("persistent log backup is not a private regular file")
                os.replace(
                    source,
                    target,
                    src_dir_fd=self._directory_fd,
                    dst_dir_fd=self._directory_fd,
                )
            except FileNotFoundError:
                pass
        try:
            os.replace(
                self.base_path.name,
                f"{self.base_path.name}.1",
                src_dir_fd=self._directory_fd,
                dst_dir_fd=self._directory_fd,
            )
        except FileNotFoundError:
            pass
        self._open_stream_locked()

    def _write_all_locked(self, data: bytes) -> None:
        if self._stream_fd is None:
            raise OSError("persistent log stream is unavailable")
        view = memoryview(data)
        while view:
            written = os.write(self._stream_fd, view)
            if written <= 0:
                raise OSError("persistent log write made no progress")
            view = view[written:]

    def emit(self, record: logging.LogRecord) -> None:
        if self.disabled:
            return
        try:
            rendered = _safe_persistent_record(self.format(record), self.max_bytes)
            if not rendered:
                return
            if self._lock_fd is None:
                raise OSError("persistent log lock is unavailable")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                self._validate_lock_identity_locked()
                self._ensure_active_stream_locked()
                assert self._stream_fd is not None
                current_size = os.fstat(self._stream_fd).st_size
                if current_size + len(rendered) > self.max_bytes:
                    self._rollover_locked()
                self._write_all_locked(rendered)
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except Exception:
            self._disable_after_failure()

    def _disable_after_failure(self) -> None:
        if self.disabled:
            return
        self.disabled = True
        logging.getLogger().removeHandler(self)
        self._close_descriptors()
        _direct_stderr_diagnostic()

    def close(self) -> None:
        self.disabled = True
        self._close_descriptors()
        super().close()


def install_persistent_json_logging(
    config_dir: Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> InterProcessRotatingJsonHandler | None:
    """Install one persistent JSON handler, degrading silently on storage errors."""
    global _persistent_handler
    root = logging.getLogger()
    for existing in list(root.handlers):
        if not getattr(existing, "_ecm_persistent_json_handler", False):
            continue
        if not getattr(existing, "disabled", False):
            _persistent_handler = existing
            return existing
        root.removeHandler(existing)

    try:
        handler = InterProcessRotatingJsonHandler(
            Path(config_dir) / "logs" / _PERSISTENT_LOG_FILENAME,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
        from observability import JsonFormatter, _TraceIdFilter

        handler.setFormatter(JsonFormatter())
        handler.addFilter(_TraceIdFilter())
        root.addHandler(handler)
        _persistent_handler = handler
        return handler
    except Exception:
        _persistent_handler = None
        _direct_stderr_diagnostic()
        return None


def _persistent_handler_degraded() -> bool:
    root = logging.getLogger()
    return not any(
        getattr(handler, "_ecm_persistent_json_handler", False)
        and not getattr(handler, "disabled", False)
        for handler in root.handlers
    )


def get_persistent_log_policy() -> PersistentLogPolicy | None:
    """Return the live handler policy, which may differ from saved settings."""
    for handler in logging.getLogger().handlers:
        if getattr(handler, "_ecm_persistent_json_handler", False) and not getattr(
            handler, "disabled", False
        ):
            return PersistentLogPolicy(
                max_bytes=int(handler.max_bytes),
                backup_count=int(handler.backup_count),
            )
    return None


def _open_rotating_sources(
    base_path: Path,
    backup_count: int,
    directory_fd: int,
) -> tuple[list[_OpenedPersistentLogSource], bool, str | None]:
    sources: list[_OpenedPersistentLogSource] = []
    incomplete = False
    reason: str | None = None
    candidates = [
        f"{base_path.name}.{index}"
        for index in range(backup_count, 0, -1)
    ] + [base_path.name]
    for candidate_name in candidates:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                candidate_name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            continue
        except OSError:
            incomplete = True
            reason = reason or "source_open_failed"
            continue

        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise OSError("persistent log source is not regular")
            sources.append(
                _OpenedPersistentLogSource(
                    name=candidate_name,
                    descriptor=descriptor,
                    size=file_stat.st_size,
                )
            )
            descriptor = None
        except OSError:
            incomplete = True
            reason = reason or "source_open_failed"
        finally:
            if descriptor is not None:
                os.close(descriptor)

    return sources, incomplete, reason


def _read_opened_sources(
    opened_sources: list[_OpenedPersistentLogSource],
    max_total_bytes: int | None,
) -> tuple[tuple[PersistentLogSource, ...], bool, str | None, bool]:
    selected: list[tuple[_OpenedPersistentLogSource, int, int]] = []
    byte_limit_reached = False
    remaining_budget = max_total_bytes

    for source in reversed(opened_sources):
        if remaining_budget is not None and remaining_budget <= 0:
            byte_limit_reached = byte_limit_reached or source.size > 0
            os.close(source.descriptor)
            continue

        requested = source.size
        if remaining_budget is not None:
            requested = min(requested, remaining_budget)
            remaining_budget -= requested
        offset = source.size - requested
        byte_limit_reached = byte_limit_reached or offset > 0
        selected.append((source, offset, requested))

    sources: list[PersistentLogSource] = []
    incomplete = False
    reason: str | None = None
    for source, offset, requested in reversed(selected):
        data = bytearray()
        complete = True
        prefix_probe = 1 if offset > 0 else 0
        bytes_to_read = requested + prefix_probe
        try:
            os.lseek(source.descriptor, offset - prefix_probe, os.SEEK_SET)
            remaining = bytes_to_read
            while remaining:
                chunk = os.read(source.descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    complete = False
                    break
                data.extend(chunk)
                remaining -= len(chunk)
        except OSError:
            complete = False
        finally:
            os.close(source.descriptor)

        if complete and prefix_probe:
            if data[:1] == b"\n":
                del data[:1]
            else:
                newline = data.find(b"\n")
                if newline < 0:
                    data.clear()
                else:
                    del data[: newline + 1]

        if not complete or len(data) > requested:
            incomplete = True
            reason = reason or "source_read_incomplete"
        sources.append(
            PersistentLogSource(
                name=source.name,
                data=bytes(data),
                expected_bytes=len(data) if complete else requested,
                complete=complete,
            )
        )
    return tuple(sources), incomplete, reason, byte_limit_reached


def _rotation_has_discarded_files(directory_fd: int) -> tuple[bool, bool]:
    try:
        marker_stat = os.stat(
            _PERSISTENT_LOG_SATURATION_FILENAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False, False
    except OSError:
        return False, True
    valid = stat.S_ISREG(marker_stat.st_mode) and marker_stat.st_nlink == 1
    return valid, not valid


def snapshot_persistent_logs(
    config_dir: Path,
    *,
    backup_count: int,
    max_total_bytes: int | None = None,
) -> PersistentLogSnapshot:
    """Read a bounded oldest-to-newest snapshot of fixed-size descriptors.

    File descriptors and sizes are captured under the rotation lock. Bulk reads
    happen after releasing it, so peer log writers are not blocked by bundle I/O.
    """
    if max_total_bytes is not None and max_total_bytes < 1:
        raise ValueError("max_total_bytes must be positive")
    base_path = Path(config_dir) / "logs" / _PERSISTENT_LOG_FILENAME
    try:
        directory_fd = _open_secure_log_directory(base_path.parent, create=False)
    except FileNotFoundError:
        return PersistentLogSnapshot(
            (), False, None, _persistent_handler_degraded(), False
        )
    except OSError:
        return PersistentLogSnapshot(
            (), True, "source_open_failed", _persistent_handler_degraded(), False
        )

    try:
        epoch_is_valid = _epoch_marker_is_valid(directory_fd)
        unsafe = not epoch_is_valid and bool(_rotating_log_names(directory_fd))
    except OSError:
        os.close(directory_fd)
        return PersistentLogSnapshot(
            (), True, "unsafe_log_epoch", _persistent_handler_degraded(), False
        )
    if not epoch_is_valid:
        os.close(directory_fd)
        return PersistentLogSnapshot(
            (), unsafe, "unsafe_log_epoch" if unsafe else None,
            _persistent_handler_degraded(), False,
        )

    lock_fd: int | None = None
    lock_failed = False
    try:
        try:
            lock_fd = os.open(
                _PERSISTENT_LOG_LOCK_FILENAME,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            lock_fd = os.open(
                _PERSISTENT_LOG_LOCK_FILENAME,
                os.O_RDWR
                | os.O_CREAT
                | os.O_CLOEXEC
                | os.O_NOFOLLOW
                | os.O_NONBLOCK,
                0o600,
                dir_fd=directory_fd,
            )
        lock_stat = _validate_private_regular(lock_fd, label="lock")
        fcntl.flock(lock_fd, fcntl.LOCK_SH)
        named_lock = os.stat(
            _PERSISTENT_LOG_LOCK_FILENAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (named_lock.st_dev, named_lock.st_ino) != (
            lock_stat.st_dev,
            lock_stat.st_ino,
        ):
            raise OSError("persistent log lock identity changed")
    except OSError:
        lock_failed = True
        if lock_fd is not None:
            os.close(lock_fd)
            lock_fd = None

    opened_sources: list[_OpenedPersistentLogSource] = []
    incomplete = False
    reason: str | None = None
    saturated = False
    try:
        opened_sources, incomplete, reason = _open_rotating_sources(
            base_path, backup_count, directory_fd
        )
        saturated, marker_failed = _rotation_has_discarded_files(directory_fd)
        if marker_failed:
            incomplete = True
            reason = reason or "saturation_state_unavailable"
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(directory_fd)

    files, read_incomplete, read_reason, byte_limit_reached = _read_opened_sources(
        opened_sources,
        max_total_bytes,
    )

    return PersistentLogSnapshot(
        files=files,
        incomplete=incomplete or read_incomplete or lock_failed,
        reason="lock_unavailable" if lock_failed else reason or read_reason,
        handler_degraded=_persistent_handler_degraded(),
        rotation_saturated=saturated,
        byte_limit_reached=byte_limit_reached,
    )


def _reset_file_logging_for_tests() -> None:
    """Detach persistent handlers and re-arm the fixed diagnostic."""
    global _persistent_handler, _file_diagnostic_emitted
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ecm_persistent_json_handler", False):
            root.removeHandler(handler)
            handler.close()
    _persistent_handler = None
    _file_diagnostic_emitted = False


def _reset_sensitive_values_for_tests() -> None:
    with _sensitive_values_lock:
        _sensitive_value_forms.clear()
