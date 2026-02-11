"""
Unit tests for the FFMPEG Builder execution module.

Tests command execution, progress parsing, events, and safety (Spec 1.10).
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from ffmpeg_builder.execution import (
    execute_command,
    parse_progress_line,
    FFMPEGExecutor,
    ExecutionResult,
    ProgressInfo,
    ExecutionEvent,
)

from tests.fixtures.ffmpeg_factories import (
    create_builder_state,
    create_ffmpeg_job,
    create_job_progress,
)


class TestFFMPEGExecution:
    """Tests for executing FFMPEG commands."""

    @pytest.fixture
    def executor(self):
        """Create an FFMPEGExecutor instance for testing."""
        return FFMPEGExecutor()

    @pytest.fixture
    def basic_command(self):
        """A basic ffmpeg command list for testing."""
        return ["ffmpeg", "-i", "/media/input.mp4", "-c:v", "libx264", "-crf", "23", "/media/output.mp4"]

    @patch("subprocess.Popen")
    def test_executes_command_string(self, mock_popen, executor, basic_command):
        """Executor runs the provided command via subprocess."""
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = executor.run(basic_command)

        mock_popen.assert_called_once()
        assert result is not None

    @patch("subprocess.Popen")
    def test_captures_stdout(self, mock_popen, executor, basic_command):
        """Executor captures stdout output from the process."""
        mock_process = MagicMock()
        mock_process.stdout = iter(["output line 1\n", "output line 2\n"])
        mock_process.stderr = iter([])
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = executor.run(basic_command)

        assert result.stdout is not None
        assert "output line 1" in result.stdout

    @patch("subprocess.Popen")
    def test_captures_stderr(self, mock_popen, executor, basic_command):
        """Executor captures stderr output from the process."""
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter(["error: something went wrong\n"])
        mock_process.wait.return_value = 1
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        result = executor.run(basic_command)

        assert result.stderr is not None
        assert "error" in result.stderr.lower()

    @patch("subprocess.Popen")
    def test_returns_exit_code(self, mock_popen, executor, basic_command):
        """Executor returns the process exit code."""
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = executor.run(basic_command)

        assert result.exit_code == 0

    @patch("subprocess.Popen")
    def test_handles_process_timeout(self, mock_popen, executor, basic_command):
        """Executor handles timeout by killing the process and returning an error."""
        mock_process = MagicMock()
        mock_process.wait.side_effect = TimeoutError("Process timed out")
        mock_process.kill.return_value = None
        mock_process.returncode = -9
        mock_popen.return_value = mock_process

        result = executor.run(basic_command, timeout=5)

        assert result.exit_code != 0
        mock_process.kill.assert_called()

    @patch("subprocess.Popen")
    def test_kills_on_cancel(self, mock_popen, executor, basic_command):
        """Executor kills the process when cancel is requested."""
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.poll.return_value = None
        mock_process.kill.return_value = None
        mock_process.returncode = -15
        mock_popen.return_value = mock_process

        executor.cancel()
        mock_process.kill.assert_called() or mock_process.terminate.assert_called()

    def test_rejects_empty_command(self, executor):
        """Executor rejects an empty command list."""
        with pytest.raises((ValueError, TypeError)):
            executor.run([])


class TestProgressParsing:
    """Tests for parsing FFMPEG progress output lines."""

    def test_parses_frame_count(self):
        """Parses 'frame=' field from progress line."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:00:16.67 bitrate=5027.0kbits/s speed=1.0x"
        info = parse_progress_line(line)

        assert info.frame == 500

    def test_parses_fps(self):
        """Parses 'fps=' field from progress line."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:00:16.67 bitrate=5027.0kbits/s speed=1.0x"
        info = parse_progress_line(line)

        assert info.fps == pytest.approx(30.0)

    def test_parses_speed(self):
        """Parses 'speed=' field from progress line."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:00:16.67 bitrate=5027.0kbits/s speed=2.5x"
        info = parse_progress_line(line)

        assert info.speed == "2.5x"

    def test_parses_time_position(self):
        """Parses 'time=' field from progress line."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:05:30.00 bitrate=5027.0kbits/s speed=1.0x"
        info = parse_progress_line(line)

        assert info.time == "00:05:30.00"

    def test_parses_output_size(self):
        """Parses 'size=' field from progress line."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:00:16.67 bitrate=5027.0kbits/s speed=1.0x"
        info = parse_progress_line(line)

        assert "10240" in str(info.size)

    def test_parses_bitrate(self):
        """Parses 'bitrate=' field from progress line."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:00:16.67 bitrate=5027.0kbits/s speed=1.0x"
        info = parse_progress_line(line)

        assert "5027" in str(info.bitrate)

    def test_calculates_percent_from_duration(self):
        """Progress percent is calculated from current time and total duration."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:05:00.00 bitrate=5027.0kbits/s speed=1.0x"
        total_duration = "00:10:00.00"
        info = parse_progress_line(line, total_duration=total_duration)

        assert info.percent == pytest.approx(50.0, abs=1.0)

    def test_calculates_eta(self):
        """ETA is calculated from progress speed and remaining duration."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:05:00.00 bitrate=5027.0kbits/s speed=2.0x"
        total_duration = "00:10:00.00"
        info = parse_progress_line(line, total_duration=total_duration)

        # At 2.0x speed with 5min remaining, ETA should be ~2.5 min
        assert info.eta is not None

    def test_handles_unknown_duration(self):
        """When total duration is unknown, percent and ETA are None."""
        line = "frame=  500 fps=30.0 q=28.0 size=   10240kB time=00:05:00.00 bitrate=5027.0kbits/s speed=1.0x"
        info = parse_progress_line(line, total_duration=None)

        assert info.percent is None
        assert info.eta is None


class TestExecutionEvents:
    """Tests for execution event emission during FFMPEG processing."""

    @pytest.fixture
    def executor(self):
        """Create an FFMPEGExecutor with event tracking."""
        return FFMPEGExecutor()

    @pytest.fixture
    def basic_command(self):
        return ["ffmpeg", "-i", "/media/input.mp4", "-c:v", "libx264", "/media/output.mp4"]

    @patch("subprocess.Popen")
    def test_emits_progress_event(self, mock_popen, executor, basic_command):
        """Executor emits progress events as encoding advances."""
        events = []
        executor.on_event(lambda e: events.append(e))

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([
            "frame=  100 fps=30.0 q=28.0 size=    2048kB time=00:00:03.33 bitrate=5027.0kbits/s speed=1.0x\n",
        ])
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor.run(basic_command)

        progress_events = [e for e in events if e.type == "progress"]
        assert len(progress_events) > 0

    @patch("subprocess.Popen")
    def test_emits_started_event(self, mock_popen, executor, basic_command):
        """Executor emits a 'started' event when encoding begins."""
        events = []
        executor.on_event(lambda e: events.append(e))

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor.run(basic_command)

        started_events = [e for e in events if e.type == "started"]
        assert len(started_events) == 1

    @patch("subprocess.Popen")
    def test_emits_completed_event(self, mock_popen, executor, basic_command):
        """Executor emits a 'completed' event when encoding finishes successfully."""
        events = []
        executor.on_event(lambda e: events.append(e))

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor.run(basic_command)

        completed_events = [e for e in events if e.type == "completed"]
        assert len(completed_events) == 1

    @patch("subprocess.Popen")
    def test_emits_failed_event(self, mock_popen, executor, basic_command):
        """Executor emits a 'failed' event when encoding fails."""
        events = []
        executor.on_event(lambda e: events.append(e))

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter(["Error: encoding failed\n"])
        mock_process.wait.return_value = 1
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        executor.run(basic_command)

        failed_events = [e for e in events if e.type == "failed"]
        assert len(failed_events) == 1

    @patch("subprocess.Popen")
    def test_emits_cancelled_event(self, mock_popen, executor, basic_command):
        """Executor emits a 'cancelled' event when encoding is cancelled."""
        events = []
        executor.on_event(lambda e: events.append(e))

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.poll.return_value = None
        mock_process.kill.return_value = None
        mock_process.returncode = -15
        mock_process.wait.return_value = -15
        mock_popen.return_value = mock_process

        # Start and immediately cancel
        executor._cancelled = True
        executor.run(basic_command)

        cancelled_events = [e for e in events if e.type == "cancelled"]
        assert len(cancelled_events) == 1


class TestExecutionSafety:
    """Tests for command execution safety checks."""

    @pytest.fixture
    def executor(self):
        return FFMPEGExecutor()

    def test_sanitizes_command_injection(self, executor):
        """Command with shell injection characters is rejected or sanitized."""
        malicious_command = ["ffmpeg", "-i", "input.mp4; rm -rf /", "-c:v", "libx264", "output.mp4"]

        with pytest.raises((ValueError, SecurityError)):
            executor.run(malicious_command)

    def test_rejects_shell_metacharacters(self, executor):
        """Command arguments containing shell metacharacters are rejected."""
        dangerous_commands = [
            ["ffmpeg", "-i", "input.mp4", "&&", "echo", "pwned"],
            ["ffmpeg", "-i", "input.mp4", "|", "cat", "/etc/passwd"],
            ["ffmpeg", "-i", "input.mp4", "`cat /etc/passwd`"],
            ["ffmpeg", "-i", "input.mp4", "$(cat /etc/passwd)"],
        ]

        for cmd in dangerous_commands:
            with pytest.raises((ValueError, SecurityError)):
                executor.run(cmd)

    @patch("os.access")
    def test_validates_output_path_writable(self, mock_access, executor):
        """Executor validates that the output path directory is writable."""
        mock_access.return_value = False

        command = ["ffmpeg", "-i", "/media/input.mp4", "-c:v", "libx264", "/readonly/output.mp4"]

        with pytest.raises((ValueError, PermissionError, OSError)):
            executor.run(command)
