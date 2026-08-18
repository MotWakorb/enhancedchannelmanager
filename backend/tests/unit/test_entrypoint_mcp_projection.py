"""Preflight preparation of the MCP credential projection (…-04c0u.8).

``docker-compose.mcp.yml`` mounts a dedicated named volume at
``MCP_SECRETS_DIR`` in the **ecm** service. Docker creates a new named volume's
root as ``root:root 0755`` and a bind mount arrives owned by whatever host uid
made it, so after ``entrypoint.sh`` drops to ``gosu appuser`` the backend
cannot create ``api-key`` or ``mcp-service.json`` there.

That is not a degraded sidecar. ``backend/main.py`` materializes the private
projection inside the FastAPI startup handler, and an exception out of a
startup handler aborts the ASGI lifespan — uvicorn logs "Application startup
failed. Exiting." and the container never serves. The same call also runs in
the auth middleware on every non-exempt request, so an unwritable projection
directory is a 500 on every authenticated request as well.

``prepare_mcp_projection_dir()`` closes that by creating, chowning and
chmodding the directory while the entrypoint is still root, and by turning a
genuine failure into a named preflight error instead of an unhandled stack
trace at import time.

Layer note: shell-level behavioral tests over the real ``entrypoint.sh``. The
function takes its owner and its probe runner as parameters precisely so these
tests can drive it without root or ``gosu`` — no new operator-settable
environment seam is introduced (contrast ``ECM_MOUNTINFO``, bead
``enhancedchannelmanager-nz8q4`` item 2).
"""
import grp
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENTRYPOINT = BACKEND_DIR / "entrypoint.sh"
SH = shutil.which("sh") or "/bin/sh"


def _run_prepare(projection_dir, config_dir="/config", runner="env"):
    """Execute the real prepare_mcp_projection_dir() out of entrypoint.sh.

    ``runner`` stands in for production's ``gosu appuser``: the tests run it as
    the current account, which is the account that owns the fixture directory.
    """
    script = ENTRYPOINT.read_text()
    marker = "check_filesystem() {"
    assert marker in script, "entrypoint.sh check_filesystem moved — update test"
    assert "prepare_mcp_projection_dir() {" in script[: script.index(marker)], (
        "prepare_mcp_projection_dir must be defined before check_filesystem so "
        "the shell-level harnesses can reach it"
    )
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    harness = (
        script[: script.index(marker)]
        + f'\nprepare_mcp_projection_dir "$PROJECTION_DIR" '
        + f'"{user}" "{group}" {runner}\n'
    )
    proc = subprocess.run(
        [SH, "-c", harness],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "CONFIG_DIR": config_dir,
            "PROJECTION_DIR": str(projection_dir),
        },
    )
    return proc.returncode, proc.stdout + proc.stderr


class TestFirstRunUnprovisionedMount:
    """The invariant: a brand-new mount needs no manual privilege step."""

    def test_a_directory_the_backend_cannot_write_is_repaired(self, tmp_path):
        """The production symptom, reproduced by mode rather than by uid.

        A fresh Docker named volume is ``root:root 0755``: the ECM account has
        r-x and no w, so it cannot create the projection files. 0500 here is
        the same property — "this account cannot create a file in this
        directory" — expressed without needing root in the test runner.
        """
        projection = tmp_path / "run" / "secrets" / "ecm-mcp"
        projection.mkdir(parents=True)
        projection.chmod(0o500)
        with pytest.raises(PermissionError):
            (projection / "api-key").write_text("x")

        rc, out = _run_prepare(projection)

        assert rc == 0, out
        assert "is writable" in out
        assert stat.S_IMODE(projection.stat().st_mode) == 0o700
        # The property that matters is not the mode digits but that the
        # producer can now write the two projection files.
        (projection / "api-key").write_text("x")
        (projection / "mcp-service.json").write_text("{}")

    def test_a_missing_directory_is_created(self, tmp_path):
        projection = tmp_path / "run" / "secrets" / "ecm-mcp"

        rc, out = _run_prepare(projection)

        assert rc == 0, out
        assert projection.is_dir()
        assert stat.S_IMODE(projection.stat().st_mode) == 0o700

    def test_the_write_probe_leaves_nothing_behind(self, tmp_path):
        projection = tmp_path / "ecm-mcp"

        rc, out = _run_prepare(projection)

        assert rc == 0, out
        assert list(projection.iterdir()) == []


class TestNoOverlayDeployment:
    """Without the overlay this must be a no-op, not a second /config policy."""

    def test_an_unset_projection_dir_is_skipped(self, tmp_path):
        rc, out = _run_prepare("")

        assert rc == 0, out
        assert "MCP credential projection" not in out

    def test_a_projection_dir_equal_to_config_dir_is_left_alone(self, tmp_path):
        """CONFIG_DIR is created, chowned and probed by check_filesystem.

        Re-chmodding it to 0700 here would silently change the config volume's
        mode on every deployment that does not enable the overlay, which is
        every deployment by default (MCP_SECRETS_DIR defaults to CONFIG_DIR).
        """
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_dir.chmod(0o755)

        rc, out = _run_prepare(config_dir, config_dir=str(config_dir))

        assert rc == 0, out
        assert "MCP credential projection" not in out
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o755


class TestGenuineFailureIsANamedPreflightError:
    """A failure that preparation cannot fix must name itself, not stack-trace."""

    def test_an_uncreatable_directory_fails_preflight_by_name(self, tmp_path):
        parent = tmp_path / "readonly"
        parent.mkdir()
        parent.chmod(0o555)
        try:
            rc, out = _run_prepare(parent / "ecm-mcp")
        finally:
            parent.chmod(0o755)

        assert rc == 1
        assert "Failed to create MCP credential projection directory" in out

    def test_an_unwritable_directory_fails_preflight_by_name(self, tmp_path):
        """``false`` stands in for an account that cannot write the mount."""
        projection = tmp_path / "ecm-mcp"

        rc, out = _run_prepare(projection, runner="false")

        assert rc == 1
        assert "is not writable by" in out
        assert str(projection) in out
        # The message has to tell the operator what to do about it.
        assert "chown" in out
        assert "PUID" in out and "PGID" in out
