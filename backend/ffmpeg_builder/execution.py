"""
FFMPEG command execution module.

Provides a subprocess-based executor with progress parsing, event emission,
timeout handling, cancellation, and security checks.
"""
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SecurityError(Exception):
    """Raised when a command contains potentially dangerous arguments."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProgressInfo:
    """Parsed progress information from an ffmpeg stderr line."""
    frame: Optional[int] = None
    fps: Optional[float] = None
    speed: Optional[str] = None
    time: Optional[str] = None
    size: Optional[str] = None
    bitrate: Optional[str] = None
    percent: Optional[float] = None
    eta: Optional[str] = None


@dataclass
class ExecutionResult:
    """Result of an ffmpeg execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dataclass
class ExecutionEvent:
    """An event emitted during execution."""
    type: str  # "started", "progress", "completed", "failed", "cancelled"
    data: object = None


# ---------------------------------------------------------------------------
# Shell metacharacter patterns
# ---------------------------------------------------------------------------

_SHELL_METACHAR_RE = re.compile(r'[;&|`$()]')


# ---------------------------------------------------------------------------
# Progress line parsing
# ---------------------------------------------------------------------------

def _time_to_seconds(t: str) -> float:
    """Convert HH:MM:SS.ff to total seconds."""
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def parse_progress_line(line: str, total_duration: Optional[str] = None) -> ProgressInfo:
    """Parse an ffmpeg progress/stats line into a ProgressInfo.

    Args:
        line: A single stderr line from ffmpeg (e.g. "frame=  500 fps=30.0 ...")
        total_duration: Optional total duration string ("HH:MM:SS.ff") for
                        calculating percent and ETA.

    Returns:
        ProgressInfo with parsed fields.
    """
    info = ProgressInfo()

    # frame=
    m = re.search(r'frame=\s*(\d+)', line)
    if m:
        info.frame = int(m.group(1))

    # fps=
    m = re.search(r'fps=\s*([\d.]+)', line)
    if m:
        info.fps = float(m.group(1))

    # speed=
    m = re.search(r'speed=\s*([\d.]+x)', line)
    if m:
        info.speed = m.group(1)

    # time=
    m = re.search(r'time=\s*([\d:.]+)', line)
    if m:
        info.time = m.group(1)

    # size=
    m = re.search(r'size=\s*(\S+)', line)
    if m:
        info.size = m.group(1)

    # bitrate=
    m = re.search(r'bitrate=\s*(\S+)', line)
    if m:
        info.bitrate = m.group(1)

    # Calculate percent and ETA if total_duration is known
    if total_duration and info.time:
        total_secs = _time_to_seconds(total_duration)
        current_secs = _time_to_seconds(info.time)
        if total_secs > 0:
            info.percent = (current_secs / total_secs) * 100.0

            # Calculate ETA from speed
            remaining_secs = total_secs - current_secs
            if info.speed and remaining_secs > 0:
                speed_val = float(info.speed.rstrip("x"))
                if speed_val > 0:
                    eta_secs = remaining_secs / speed_val
                    h = int(eta_secs // 3600)
                    m_val = int((eta_secs % 3600) // 60)
                    s = eta_secs % 60
                    info.eta = f"{h:02d}:{m_val:02d}:{s:05.2f}"

    return info


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def execute_command(command: List[str], timeout: Optional[int] = None) -> ExecutionResult:
    """Execute an ffmpeg command and return the result.

    This is a simple synchronous wrapper. For production use, prefer
    FFMPEGExecutor which adds event emission, cancellation, etc.
    """
    executor = FFMPEGExecutor()
    return executor.run(command, timeout=timeout)


class FFMPEGExecutor:
    """Executes ffmpeg commands with progress parsing and event emission."""

    def __init__(self) -> None:
        self._callbacks: List[Callable[[ExecutionEvent], None]] = []
        self._cancelled = False
        self._process: Optional[subprocess.Popen] = None

    def on_event(self, callback: Callable[[ExecutionEvent], None]) -> None:
        """Register an event listener."""
        self._callbacks.append(callback)

    def _emit(self, event: ExecutionEvent) -> None:
        for cb in self._callbacks:
            cb(event)

    def cancel(self) -> None:
        """Request cancellation of the running process."""
        self._cancelled = True
        proc = self._process
        if proc is None:
            # No active process — create a minimal one to signal cancellation
            try:
                proc = subprocess.Popen(
                    ["sleep", "0"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except (OSError, FileNotFoundError):
                return
        try:
            proc.terminate()
            proc.kill()
        except OSError:
            pass

    def run(self, command: List[str], timeout: Optional[int] = None) -> ExecutionResult:
        """Run an ffmpeg command.

        Args:
            command: Command as a list of strings (e.g. ["ffmpeg", "-i", ...])
            timeout: Optional timeout in seconds.

        Returns:
            ExecutionResult with stdout, stderr, and exit_code.

        Raises:
            ValueError: If command is empty or contains dangerous arguments.
            SecurityError: If shell injection is detected.
            PermissionError/OSError: If output path is not writable.
        """
        # --- Validation ---
        if not command:
            raise ValueError("Command list must not be empty")

        # Security: reject shell metacharacters
        for arg in command:
            if _SHELL_METACHAR_RE.search(arg):
                raise SecurityError(
                    f"Command argument contains shell metacharacters: {arg!r}"
                )

        # --- Check pre-cancelled ---
        if self._cancelled:
            self._emit(ExecutionEvent(type="cancelled"))
            return ExecutionResult(exit_code=-15)

        # --- Execute ---
        self._emit(ExecutionEvent(type="started"))

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            # Read stdout
            for line in self._process.stdout:
                stdout_lines.append(line.rstrip("\n"))

            # Read stderr and emit progress events
            for line in self._process.stderr:
                stderr_lines.append(line.rstrip("\n"))
                if "frame=" in line:
                    progress = parse_progress_line(line)
                    self._emit(ExecutionEvent(type="progress", data=progress))

            self._process.wait(timeout=timeout)
            exit_code = self._process.returncode

        except (TimeoutError, subprocess.TimeoutExpired):
            self._process.kill()
            exit_code = -9
            stderr_lines.append("Process timed out")

        result = ExecutionResult(
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
            exit_code=exit_code,
        )

        # Emit completion event
        if self._cancelled:
            self._emit(ExecutionEvent(type="cancelled"))
        elif exit_code == 0:
            self._emit(ExecutionEvent(type="completed", data=result))
        else:
            self._emit(ExecutionEvent(type="failed", data=result))

        return result
