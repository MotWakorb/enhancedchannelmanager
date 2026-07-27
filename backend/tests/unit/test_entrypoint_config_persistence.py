"""Config-persistence preflight tests (bead enhancedchannelmanager-ebl4n).

Same field case as ``test_entrypoint_interpreter.py``. While diagnosing why
that operator's container would not start, ``docker inspect --format
'{{json .Mounts}}'`` came back as ``[]`` — no bind mount, no named volume.
Everything ECM persists (``settings.json``, ``journal.db``, uploaded logos,
TLS material) lived only in the container's writable layer, while preflight
cheerfully reported::

    ✓ Config directory exists
    ✓ Config directory is writable

Both statements are true and both are dangerously reassuring: the container
recreate that fixes their startup problem would also have destroyed every
byte of their data. Preflight now says so — loudly, and non-fatally, because
a first-run or throwaway container legitimately has no mount.

Detection reads ``/proc/self/mountinfo`` (field 5 is the mount point), which
is the only place both bind mounts and named volumes reliably show up from
inside the container. The fixtures under ``tests/fixtures/mountinfo/`` are
**recorded from real containers** of the published image — no-mount, named
volume, bind mount, and a volume mounted at a parent of CONFIG_DIR — rather
than hand-written shapes.

Layer note: shell-level behavioral tests over the real ``entrypoint.sh``,
driven by recorded real ``mountinfo`` files.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENTRYPOINT = BACKEND_DIR / "entrypoint.sh"
MOUNTINFO_FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "mountinfo"
SH = shutil.which("sh") or "/bin/sh"


def _run_check(config_dir, mountinfo=None, extra_env=None):
    """Execute the real check_config_persistence() out of entrypoint.sh."""
    script = ENTRYPOINT.read_text()
    marker = "check_filesystem() {"
    assert marker in script, "entrypoint.sh check_filesystem moved — update test"
    harness = script[: script.index(marker)] + "\ncheck_config_persistence\n"
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "CONFIG_DIR": config_dir,
    }
    if mountinfo is not None:
        env["ECM_MOUNTINFO"] = str(mountinfo)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [SH, "-c", harness], capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _fixture(name):
    path = MOUNTINFO_FIXTURES / f"{name}.txt"
    assert path.exists(), f"missing recorded mountinfo fixture: {path}"
    return path


class TestMountedConfigStaysQuiet:
    """A correctly-mounted container must not be nagged."""

    @pytest.mark.parametrize(
        "fixture",
        ["container_named_volume", "container_bind_mount"],
    )
    def test_named_volume_and_bind_mount_are_recognised(self, fixture):
        rc, out = _run_check("/config", _fixture(fixture))
        assert rc == 0
        assert "DATA IS NOT PERSISTENT" not in out
        assert "persists" in out

    def test_mount_on_a_parent_directory_counts(self):
        # CONFIG_DIR=/srv/ecm/config under a volume mounted at /srv/ecm:
        # the data IS durable, so warning here would be crying wolf.
        rc, out = _run_check("/srv/ecm/config", _fixture("container_mounted_parent"))
        assert rc == 0
        assert "DATA IS NOT PERSISTENT" not in out

    def test_unreadable_mountinfo_does_not_warn(self, tmp_path):
        # If we cannot tell, stay quiet — a false "your data is ephemeral"
        # is worse than no message.
        rc, out = _run_check("/config", tmp_path / "nope")
        assert rc == 0
        assert "DATA IS NOT PERSISTENT" not in out


class TestUnmountedConfigWarnsLoudly:
    """The field case: no mounts at all."""

    def test_writable_layer_only_is_reported(self):
        rc, out = _run_check("/config", _fixture("container_no_mount"))
        # Non-fatal: a first-run/throwaway container is legitimate.
        assert rc == 0, "the mount warning must never fail preflight"
        assert "DATA IS NOT PERSISTENT" in out
        assert "/config" in out
        # Says what is at stake, in the operator's vocabulary...
        assert "settings.json" in out
        assert "journal.db" in out
        # ...names the exact event that destroys it...
        assert "recreate" in out.lower()
        # ...and tells them how to fix it, plus to back up before doing the
        # container recreate the 0oi96 fix tells them to do.
        assert "-v ecm-config:/config" in out
        assert "Back up first" in out

    def test_sibling_mounts_are_not_mistaken_for_config(self):
        # The no-mount fixture still contains /proc, /dev, /etc/hosts and
        # friends — a naive substring match over mountinfo would call that
        # a mounted /config.
        _, out = _run_check("/config", _fixture("container_no_mount"))
        assert "DATA IS NOT PERSISTENT" in out

    def test_config_dir_prefix_collision_does_not_count(self, tmp_path):
        # /config-backup must not satisfy the check for /config.
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "1 0 0:1 / / rw - overlay overlay rw\n"
            "2 1 0:2 / /config-backup rw - ext4 /dev/sda1 rw\n"
        )
        _, out = _run_check("/config", mountinfo)
        assert "DATA IS NOT PERSISTENT" in out


class TestPreflightWiring:
    def test_check_runs_as_part_of_preflight(self):
        script = ENTRYPOINT.read_text()
        preflight = script[script.index("run_preflight_checks() {"):]
        assert "check_config_persistence" in preflight

    def test_check_cannot_fail_preflight(self):
        # It must not be chained into FAILED=1 like the fatal checks.
        script = ENTRYPOINT.read_text()
        preflight = script[script.index("run_preflight_checks() {"):]
        line = next(
            l for l in preflight.splitlines() if "check_config_persistence" in l
        )
        assert "FAILED=1" not in line, (
            "the mount warning is advisory — a container without a mount is "
            "legitimate and must still start"
        )
