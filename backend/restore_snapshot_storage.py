"""Private saved-restore storage with cross-process leases (sumq7).

Only the known flat snapshot layout is reclaimed; no recursive deletion is used
by the sweeper. The config parent must remain trusted against same-UID writers.
"""

import fcntl
import os
import re
import stat
import tempfile
import weakref
from contextlib import AbstractContextManager
from pathlib import Path

_DIRECTORY_RE = re.compile(r"ecm-saved-restore-[a-z0-9_]{8}\Z")
_ARTIFACT_RE = re.compile(r"ecm-restore-[a-z0-9_]{8}\.zip\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class PrivateRestoreSnapshot(AbstractContextManager):
    """Keep an advisory directory lease until wrapper/callback cleanup finishes."""

    def __init__(self, parent: Path):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="ecm-saved-restore-", dir=parent
        )
        self.name = self._temporary.name
        descriptor = None
        try:
            descriptor = os.open(self.name, _DIRECTORY_FLAGS)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._release = weakref.finalize(self, os.close, descriptor)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            self._temporary.cleanup()
            raise

    def cleanup(self):
        try:
            self._temporary.cleanup()
        finally:
            self._release()

    def __exit__(self, *exc):
        self.cleanup()


def sweep_private_restore_snapshots(parent: Path, cutoff: float) -> None:
    """Remove old unlocked private snapshots, refusing unknown/unsafe layouts.

    Triggered by the existing restore sweep, including saved-restore requests.
    Fresh files and directories, live leases, symlinks and unexpected entries are
    preserved. The router's best-effort sweep boundary handles filesystem errors.
    """
    root_fd = os.open(parent, _DIRECTORY_FLAGS)
    try:
        root = os.fstat(root_fd)
        if root.st_uid != os.geteuid() or root.st_mode & 0o022:
            return
        for name in os.listdir(root_fd):
            if not _DIRECTORY_RE.fullmatch(name):
                continue
            try:
                _reclaim(root_fd, name, cutoff)
            except OSError:
                # A raced, leased, inaccessible or malformed candidate is not
                # permission to broaden deletion or block a legitimate restore.
                continue
    finally:
        os.close(root_fd)


def _private(info, mode: int) -> bool:
    return info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == mode


def _reclaim(root_fd: int, name: str, cutoff: float) -> None:
    directory_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=root_fd)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = os.fstat(directory_fd)
        if not _private(directory, 0o700) or directory.st_mtime >= cutoff:
            return
        names = os.listdir(directory_fd)
        # A producer creates at most one ZIP. Interrupted pre-copy creation can
        # leave an empty directory; anything else is outside our ownership claim.
        if len(names) > 1:
            return
        for artifact in names:
            if not _ARTIFACT_RE.fullmatch(artifact):
                return
            info = os.stat(artifact, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or not _private(info, 0o600)
                or info.st_nlink != 1
                or info.st_mtime >= cutoff
            ):
                return
        for artifact in names:
            os.unlink(artifact, dir_fd=directory_fd)
        # Never remove a replacement name after a directory rename/rebind.
        current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == (directory.st_dev, directory.st_ino):
            os.rmdir(name, dir_fd=root_fd)
    finally:
        os.close(directory_fd)
