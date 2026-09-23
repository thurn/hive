"""Locks for source maintenance and the disposable observation cache only."""

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hive.errors import ErrorCode, HiveError


@contextmanager
def file_lock(
    path: Path, *, shared: bool = False, timeout: float = 5
) -> Iterator[None]:
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
