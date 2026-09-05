"""Validated process-shared advisory lock for the resdet native pipeline."""

from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path
import stat


RESDET_PIPELINE_LOCK_PATH = Path("/run/ecm/resdet.pipeline.lock")
LOCK_ERROR = "resdet pipeline lock is unavailable"


class ResdetLockError(RuntimeError):
    """The fixed operator-safe failure for an unusable lock path."""


class ResdetPipelineLock:
    def __init__(
        self,
        path: Path = RESDET_PIPELINE_LOCK_PATH,
        *,
        poll_interval: float = 0.05,
    ) -> None:
        self.path = Path(path)
        self.poll_interval = poll_interval
        self._fd: int | None = None

    async def acquire(self) -> "ResdetPipelineLock":
        if self._fd is not None:
            raise ResdetLockError(LOCK_ERROR)
        flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OSError("unsafe lock file")
        except OSError as exc:
            if "fd" in locals():
                os.close(fd)
            raise ResdetLockError(LOCK_ERROR) from exc

        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return self
                except BlockingIOError:
                    await asyncio.sleep(self.poll_interval)
        except BaseException:
            os.close(fd)
            raise

    def fileno(self) -> int:
        if self._fd is None:
            raise ResdetLockError(LOCK_ERROR)
        return self._fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    async def __aenter__(self) -> "ResdetPipelineLock":
        return await self.acquire()

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()
