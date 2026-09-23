"""Short process-shared locks and a maintenance write barrier; no daemon."""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError


@contextmanager
def file_lock(
    path: Path, *, shared: bool = False, timeout: float = 2
) -> Iterator[None]:
    """Never unlink this file: the permanent inode is the shared lock identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        while True:
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HiveError(
                        ErrorCode.BUSY, "Local state lock is busy"
                    ) from error
                time.sleep(min(0.005, remaining))
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@dataclass(frozen=True)
class Guards:
    """One canonical host-local directory, outside synchronized task data."""

    directory: Path

    @property
    def stop(self) -> Path:
        return self.directory / "maintenance.stop"

    @contextmanager
    def mutation(self) -> Iterator[None]:
        with file_lock(self.directory / "maintenance.lock", shared=True):
            if self.stop.exists():
                raise HiveError(
                    ErrorCode.PAUSED, "State maintenance has stopped mutations"
                )
            yield

    @contextmanager
    def admission(self) -> Iterator[None]:
        with file_lock(self.directory / "admission.lock"):
            yield

    def stop_mutations(self, reason: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.stop.open("w") as stream:
            stream.write(reason + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        """Successful exit means conversion and validation completed.

        The caller records the stop first. Recheck under the exclusive guard:
        another maintainer may have completed while this request was waiting.
        Such a superseded request must record a new stop before retrying.
        """
        with file_lock(self.directory / "maintenance.lock"):
            if not self.stop.exists():
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Stop mutations before maintenance"
                )
            yield
            self.stop.unlink()
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
