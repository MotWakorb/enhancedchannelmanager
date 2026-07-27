"""Entrypoint interpreter-resolution tests (bead enhancedchannelmanager-0oi96).

Field report, 2026-07-27 (operator on 0.18.0, container would not start)::

    ✓ Python 3.12.13 found
    ✗ Required Python packages missing
    ...
    ModuleNotFoundError: No module named 'fastapi'

The published image was sound. ``backend/entrypoint.sh`` resolved its
interpreter purely through ``PATH``, so an operator-side ``PATH`` override
(or a bind mount shadowing ``/opt/venv``) silently demoted every preflight
check — and the ``uvicorn`` launch — to the Debian system interpreter, which
reports a healthy-looking version string while having none of ECM's packages.
Reproduced byte-for-byte with::

    docker run --rm -e PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \\
        ghcr.io/motwakorb/enhancedchannelmanager:latest

The fix resolves ``ECM_PYTHON`` / ``ECM_UVICORN`` once, pinned to the image
virtualenv, and makes the failure branch self-diagnosing. These tests run the
*real* preflight code out of ``entrypoint.sh`` in a POSIX shell — same
extract-and-execute pattern as ``test_event_loop_selection.py`` — under the
operator's PATH: system directories present, ``/opt/venv/bin`` absent, and a
decoy ``python3`` standing in for the package-less system interpreter.

Layer note: these are shell-level behavioral tests over the real script. The
``/opt/venv`` default itself is pinned by a text guard here and verified at
container level against the published image (see the bead / PR).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENTRYPOINT = BACKEND_DIR / "entrypoint.sh"
# Resolved from the real environment: the harness below hands the script a
# deliberately stripped PATH, which would otherwise hide the shell itself.
SH = shutil.which("sh") or "/bin/sh"

# A stand-in for the Debian system python3 the field case fell through to:
# answers --version with a reassuring string, can report sys.prefix, and has
# none of ECM's packages.
DECOY_PYTHON3 = """#!/bin/sh
if [ "$1" = "--version" ]; then
    echo "Python 3.12.13"
    exit 0
fi
if [ "$1" = "-c" ]; then
    case "$2" in
        *sys.prefix*) echo "/usr"; exit 0 ;;
        *) echo "ModuleNotFoundError: No module named 'fastapi'" >&2; exit 1 ;;
    esac
fi
exit 1
"""


def _preflight_prelude():
    """Everything in entrypoint.sh up to (not including) check_filesystem:
    the print helpers, the interpreter-resolution block, the diagnostics
    helper, and check_python itself."""
    script = ENTRYPOINT.read_text()
    marker = "check_filesystem() {"
    assert marker in script, "entrypoint.sh check_filesystem moved — update test"
    return script[: script.index(marker)]


def _run_check_python(env):
    """Execute the real check_python() from entrypoint.sh under ``env``.

    ``env`` fully replaces the environment, so the PATH the script sees is
    exactly what the test declares — that is the whole point of the bug.
    """
    harness = _preflight_prelude() + "\ncheck_python\n"
    proc = subprocess.run(
        [SH, "-c", harness], capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture
def decoy_bin(tmp_path):
    """A directory holding a package-less ``python3``, to be put first on
    PATH. Mirrors the field case: the standard system directories are still
    present (``cut``, ``sh`` and friends resolve fine) — it is only the
    virtualenv that PATH no longer points at."""
    bin_dir = tmp_path / "decoy-bin"
    bin_dir.mkdir()
    shim = bin_dir / "python3"
    shim.write_text(DECOY_PYTHON3)
    shim.chmod(0o755)
    return str(bin_dir)


@pytest.fixture
def decoy_path(decoy_bin):
    """The operator's stripped PATH: system directories, no ``/opt/venv/bin``,
    and a ``python3`` that has none of ECM's packages."""
    return f"{decoy_bin}:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class TestPreflightUnderStrippedPath:
    """The regression the field case proved: preflight must not depend on
    PATH to find ECM's interpreter."""

    def test_preflight_passes_when_path_hides_the_real_interpreter(self, decoy_path):
        # PATH resolves python3 to an interpreter with none of ECM's
        # packages — exactly the operator's `docker run -e PATH=...` case.
        # The pinned interpreter must win anyway.
        rc, out = _run_check_python(
            {"PATH": decoy_path, "ECM_PYTHON": sys.executable}
        )
        assert rc == 0, f"preflight failed under stripped PATH:\n{out}"
        assert "FastAPI and Uvicorn available" in out
        # Self-diagnosing on success too: the resolved path is printed next
        # to the version, so a log paste always says which python ran.
        assert sys.executable in out

    def test_falls_back_to_path_when_pinned_interpreter_is_absent(self, decoy_bin, decoy_path):
        # No /opt/venv on the test host and no ECM_PYTHON override, so the
        # fallback must still find *an* interpreter rather than erroring out
        # — images without the venv keep their pre-fix behavior.
        rc, out = _run_check_python({"PATH": decoy_path})
        assert "Python 3.12.13 found at" in out
        assert f"{decoy_bin}/python3" in out
        # ...and that interpreter genuinely lacks the packages, so preflight
        # still fails — loudly, with diagnostics.
        assert rc == 1


class TestFailureDiagnostics:
    """The highest-value half of the fix: one log paste has to be enough."""

    def test_missing_packages_emits_full_interpreter_diagnostics(self, decoy_bin, decoy_path):
        rc, out = _run_check_python({"PATH": decoy_path})
        assert rc == 1
        assert "Required Python packages missing from" in out
        # Which interpreter actually ran
        assert f"resolved python : {decoy_bin}/python3" in out
        assert "resolved uvicorn:" in out
        # Where it thinks it lives
        assert "sys.prefix      : /usr" in out
        # The environment that chose it
        assert f"PATH            : {decoy_path}" in out
        # Whether the image virtualenv is still where ECM expects it
        assert "/opt/venv/bin/python: MISSING" in out
        # And what the operator should do about it
        assert "bind-mount over /opt/venv" in out

    def test_unrunnable_interpreter_is_reported_not_silently_skipped(self, tmp_path):
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        rc, out = _run_check_python(
            {"PATH": str(empty), "ECM_PYTHON": "/nonexistent/python"}
        )
        assert rc == 1
        assert "Python 3 not found" in out
        assert "/nonexistent/python" in out
        assert "resolved python :" in out


class TestInterpreterPinningIsStructural:
    """Text-level guards so a future edit cannot reintroduce a PATH-resolved
    interpreter (same belt-and-suspenders convention as the quoting guards in
    test_event_loop_selection.py)."""

    def test_defaults_pin_the_image_virtualenv(self):
        script = ENTRYPOINT.read_text()
        assert "VENV_BIN=/opt/venv/bin" in script
        assert 'ECM_PYTHON="${ECM_PYTHON:-${VENV_BIN}/python}"' in script
        assert 'ECM_UVICORN="${ECM_UVICORN:-${VENV_BIN}/uvicorn}"' in script

    def test_launch_execs_the_resolved_uvicorn(self):
        # uvicorn exists ONLY in /opt/venv/bin, so fixing preflight alone
        # would just move the field failure one line down.
        script = ENTRYPOINT.read_text()
        assert 'exec gosu appuser "$ECM_UVICORN" main:app' in script
        assert "exec gosu appuser uvicorn" not in script

    def test_no_bare_interpreter_invocations_remain(self):
        # Matches an interpreter being *invoked* (`python3 -c`, `python --version`)
        # rather than merely named (`command -v python3`, comments).
        invocation = re.compile(r"(?<![\w/$\"-])python3?\s+(-c|--version)\b")
        offenders = [
            (n, line)
            for n, line in enumerate(ENTRYPOINT.read_text().splitlines(), 1)
            if invocation.search(line) and not line.lstrip().startswith("#")
        ]
        assert not offenders, (
            "entrypoint.sh must invoke \"$ECM_PYTHON\", never a PATH-resolved "
            f"interpreter (bead 0oi96). Offending lines: {offenders}"
        )

    def test_dockerfile_healthcheck_pins_the_interpreter(self):
        dockerfile = (BACKEND_DIR.parent / "Dockerfile").read_text()
        healthcheck = dockerfile[dockerfile.index("HEALTHCHECK"):]
        assert "CMD /opt/venv/bin/python -c" in healthcheck
        assert "CMD python -c" not in healthcheck
