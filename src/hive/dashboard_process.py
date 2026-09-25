"""Bounded read-only provider output held only in this invocation's memory."""

import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError


@dataclass(frozen=True)
class Output:
    content: bytes
    truncated: bool


def run(
    command: tuple[str, ...],
    directory: Path,
    timeout: float,
    *,
    environment: dict[str, str] | None = None,
    cap: int = 1_048_576,
) -> Output:
    try:
        process = subprocess.Popen(
            command,
            cwd=directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise HiveError(
            ErrorCode.PROVIDER_UNAVAILABLE, "Provider unavailable"
        ) from error
    output = bytearray()
    try:
        stream = process.stdout
        if stream is None:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider output unavailable"
            )
        deadline = time.monotonic() + max(0.001, timeout)
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise HiveError(
                        ErrorCode.DELIVERY_TIMEOUT, "Provider read timed out"
                    )
                chunk = os.read(stream.fileno(), min(65536, cap + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > cap:
                    return Output(bytes(output[:cap]), True)
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise HiveError(ErrorCode.PROVIDER_UNAVAILABLE, "Provider read failed")
        return Output(bytes(output), False)
    except subprocess.TimeoutExpired as error:
        raise HiveError(
            ErrorCode.DELIVERY_TIMEOUT, "Provider read timed out"
        ) from error
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
