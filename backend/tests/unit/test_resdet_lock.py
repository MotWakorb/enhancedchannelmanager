"""Process-wide resdet pipeline lock invariants."""

from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from resdet_lock import ResdetLockError, ResdetPipelineLock


@pytest.mark.asyncio
async def test_real_second_python_process_contends_on_production_lock(tmp_path: Path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    async with ResdetPipelineLock(lock_path, poll_interval=0.01):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl, os, sys; "
                    "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW); "
                    "\ntry: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)"
                    "\nexcept BlockingIOError: raise SystemExit(23)"
                    "\nraise SystemExit(0)"
                ),
                str(lock_path),
            ],
            check=False,
        )
        assert probe.returncode == 23

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, os, sys; "
                "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW); "
                "fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)"
            ),
            str(lock_path),
        ],
        check=False,
    )
    assert probe.returncode == 0


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_disturb_owner(tmp_path: Path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    owner = ResdetPipelineLock(lock_path, poll_interval=0.01)
    await owner.acquire()
    waiter = ResdetPipelineLock(lock_path, poll_interval=0.01)
    waiting = asyncio.create_task(waiter.acquire())
    await asyncio.sleep(0.03)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    contender = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(contender)
    owner.release()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "mode", "owner"])
@pytest.mark.asyncio
async def test_unsafe_lock_file_fails_closed(tmp_path: Path, kind: str, monkeypatch):
    lock_path = tmp_path / "resdet.pipeline.lock"
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")
    target.chmod(0o600)
    if kind == "symlink":
        lock_path.symlink_to(target)
    elif kind == "hardlink":
        lock_path.hardlink_to(target)
    elif kind == "fifo":
        os.mkfifo(lock_path, 0o600)
    else:
        lock_path.write_text("", encoding="utf-8")
        lock_path.chmod(0o666 if kind == "mode" else 0o600)
        if kind == "owner":
            monkeypatch.setattr(os, "geteuid", lambda: os.stat(lock_path).st_uid + 1)

    with pytest.raises(ResdetLockError, match="resdet pipeline lock is unavailable"):
        await ResdetPipelineLock(lock_path, poll_interval=0.01).acquire()


@pytest.mark.asyncio
async def test_stale_unlocked_pathname_is_reusable(tmp_path: Path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    lock_path.touch(mode=0o600)
    async with ResdetPipelineLock(lock_path, poll_interval=0.01):
        pass
    assert lock_path.exists()
    assert lock_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_unrelated_subprocess_does_not_inherit_lock_fd(tmp_path: Path):
    lock = ResdetPipelineLock(tmp_path / "resdet.pipeline.lock", poll_interval=0.01)
    await lock.acquire()
    try:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os,sys; os.fstat(int(sys.argv[1]))",
                str(lock.fileno()),
            ],
            check=False,
        )
        assert probe.returncode != 0
    finally:
        lock.release()


def test_parent_crash_keeps_lock_until_inherited_timeout_child_exits(tmp_path: Path):
    lock_path = tmp_path / "resdet.pipeline.lock"
    backend = Path(__file__).resolve().parents[2]
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import asyncio, os, subprocess, sys; "
                "sys.path.insert(0, sys.argv[2]); "
                "from resdet_lock import ResdetPipelineLock; "
                "lock=ResdetPipelineLock(sys.argv[1]); asyncio.run(lock.acquire()); "
                "subprocess.Popen(['/usr/bin/timeout','--signal=KILL','0.4s','sleep','10'], "
                "pass_fds=(lock.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
                "start_new_session=True); print('ready', flush=True); os._exit(0)"
            ),
            str(lock_path),
            str(backend),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout is not None
    assert parent.stdout.readline().strip() == "ready"
    assert parent.wait(timeout=2) == 0

    contender = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        time.sleep(0.6)
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(contender)
