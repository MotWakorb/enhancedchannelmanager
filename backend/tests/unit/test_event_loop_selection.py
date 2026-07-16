"""
Event-loop selection tests (bead wadu3, follow-up to GH #546 / bead xzdx9).

uvloop 0.22.1 (the newest release as of this writing) has open upstream
issues on exactly our stack: #706 (segfault in FastAPI-in-container) and
#645 (responses leaking to the WRONG request under load — data exposure).
The #645 fix is merged upstream but unreleased, and #706 remains open, so
ECM pins every uvicorn launch point to the stdlib asyncio loop via
``--loop`` / ``loop=`` with an ``ECM_UVICORN_LOOP`` env escape hatch.

Three launch points must all agree:
- ``backend/entrypoint.sh``      — main HTTP server (production path)
- ``tls/https_server.py``        — HTTPS subprocess command
- ``main.py`` ``__main__`` block — direct dev invocation

These tests guard the flag so a future uvicorn/entrypoint refactor cannot
silently fall back to uvloop via uvicorn's ``auto`` loop policy.
"""
import subprocess
from pathlib import Path
from unittest.mock import patch

from tls.https_server import HTTPSServerManager, resolve_loop_choice
from tls.settings import TLS_DIR

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_entrypoint_launch_logic(loop_env_value):
    """Execute the real launch tail of entrypoint.sh (defaults + whitelist
    case-block + exec line) in a POSIX shell, with the final ``exec gosu
    appuser uvicorn`` swapped for an argv dumper.

    Returns ``(argv, warnings)`` where ``argv`` is the exact word-split
    argument vector uvicorn would have received — so these tests catch both
    whitelist regressions AND quoting regressions (an unquoted ``--loop``
    expansion would field-split a crafted value into extra argv entries).
    """
    script = (BACKEND_DIR / "entrypoint.sh").read_text()
    marker = "ECM_LIMIT_CONCURRENCY="
    tail = script[script.index(marker):]
    exec_prefix = "exec gosu appuser uvicorn"
    assert exec_prefix in tail, "entrypoint.sh launch line moved — update test"
    tail = tail.replace(exec_prefix, "dump_argv uvicorn")
    harness = (
        'print_warning() { echo "WARN:$1"; }\n'
        'dump_argv() { for a in "$@"; do printf "ARGV:%s\\n" "$a"; done; }\n'
        + tail
    )
    env = {"PATH": "/usr/bin:/bin", "ECM_PORT": "6100"}
    if loop_env_value is not None:
        env["ECM_UVICORN_LOOP"] = loop_env_value
    proc = subprocess.run(
        ["sh", "-c", harness], capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    lines = proc.stdout.splitlines()
    argv = [l[len("ARGV:"):] for l in lines if l.startswith("ARGV:")]
    warnings = [l for l in lines if l.startswith("WARN:")]
    return argv, warnings


def _loop_value(argv):
    return argv[argv.index("--loop") + 1]


class TestResolveLoopChoice:
    def test_defaults_to_asyncio(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("ECM_UVICORN_LOOP", None)
            assert resolve_loop_choice() == "asyncio"

    def test_env_override_uvloop(self):
        with patch.dict("os.environ", {"ECM_UVICORN_LOOP": "uvloop"}):
            assert resolve_loop_choice() == "uvloop"

    def test_env_override_auto(self):
        with patch.dict("os.environ", {"ECM_UVICORN_LOOP": "auto"}):
            assert resolve_loop_choice() == "auto"

    def test_invalid_value_falls_back_to_asyncio(self):
        # Whitelist: the value lands in a subprocess argv — never pass
        # arbitrary env content through.
        with patch.dict("os.environ", {"ECM_UVICORN_LOOP": "evil --oops"}):
            assert resolve_loop_choice() == "asyncio"


class TestHTTPSSubprocessCommand:
    def _command(self):
        mgr = HTTPSServerManager()
        cert = TLS_DIR / "cert.pem"
        key = TLS_DIR / "key.pem"
        return mgr._get_uvicorn_command(6143, cert, key)

    def test_command_pins_asyncio_loop_by_default(self):
        import os
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ECM_UVICORN_LOOP", None)
            cmd = self._command()
        idx = cmd.index("--loop")
        assert cmd[idx + 1] == "asyncio"

    def test_command_honors_env_override(self):
        with patch.dict("os.environ", {"ECM_UVICORN_LOOP": "uvloop"}):
            cmd = self._command()
        idx = cmd.index("--loop")
        assert cmd[idx + 1] == "uvloop"


class TestEntrypointScript:
    """Behavioral guard: run the real launch logic from entrypoint.sh and
    assert the argv uvicorn would receive."""

    def test_default_is_asyncio(self):
        argv, warnings = _run_entrypoint_launch_logic(None)
        assert argv[0] == "uvicorn"
        assert _loop_value(argv) == "asyncio"
        assert warnings == []

    def test_env_override_uvloop(self):
        argv, warnings = _run_entrypoint_launch_logic("uvloop")
        assert _loop_value(argv) == "uvloop"
        assert warnings == []

    def test_env_override_auto(self):
        argv, _ = _run_entrypoint_launch_logic("auto")
        assert _loop_value(argv) == "auto"

    def test_invalid_value_fails_closed_with_warning(self):
        # An invalid value must NOT reach uvicorn (uvicorn would reject it,
        # and set -e + exec would crash-loop the container). It must fall
        # back to asyncio with a warning — same fail-closed semantics as
        # resolve_loop_choice() on the HTTPS subprocess path.
        argv, warnings = _run_entrypoint_launch_logic("notaloop")
        assert _loop_value(argv) == "asyncio"
        assert len(warnings) == 1 and "notaloop" in warnings[0]

    def test_flag_injection_is_neutralized(self):
        # A crafted value must not smuggle extra uvicorn flags into the
        # production argv (whitelist rejects it; quoted expansion would keep
        # it a single argv word even if the whitelist were bypassed).
        crafted = "asyncio --proxy-headers --forwarded-allow-ips=*"
        argv, warnings = _run_entrypoint_launch_logic(crafted)
        assert _loop_value(argv) == "asyncio"
        assert "--proxy-headers" not in argv
        assert not any("forwarded-allow-ips" in a for a in argv)
        assert len(warnings) == 1

    def test_loop_expansion_is_quoted(self):
        # Text-level belt to the behavioral suspenders: the expansion itself
        # must stay quoted so field-splitting can never occur.
        script = (BACKEND_DIR / "entrypoint.sh").read_text()
        assert '--loop "${ECM_UVICORN_LOOP}"' in script, (
            "entrypoint.sh --loop expansion must be double-quoted"
        )


class TestDevMainInvocation:
    """Text-level guard for the __main__ dev-run block in main.py."""

    def test_dev_run_pins_loop(self):
        source = (BACKEND_DIR / "main.py").read_text()
        main_block = source.split('if __name__ == "__main__":', 1)[1]
        assert "loop=resolve_loop_choice()" in main_block, (
            "main.py __main__ uvicorn.run must pass loop=resolve_loop_choice()"
        )
