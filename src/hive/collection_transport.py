"""Resident timer and child transport; each observation batch selects fresh source."""

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from hive.collection_output import send
from hive.locking import file_lock


def environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "HIVE_SELECTED_COMMIT",
            "HIVE_SELECTED_DIRECTORY",
            "HIVE_BEADS_DIRECTORY",
            "HIVE_CLAUDE_PROJECTS",
            "HIVE_STATE_DIRECTORY",
            "HIVE_MUTATION_GUARD_FD",
            "HIVE_REPOSITORY_DIRECTORY",
        }
    }


async def read_output(stream: asyncio.StreamReader | None) -> tuple[str, bool]:
    if stream is None:
        raise ValueError("Missing collector output pipe")
    retained = bytearray()
    truncated = False
    while chunk := await stream.read(65_536):
        remaining = 65_536 - len(retained)
        retained.extend(chunk[:remaining])
        truncated |= len(chunk) > remaining
    return retained.decode("utf-8", errors="replace"), truncated


async def batch(
    launcher: Path, index: Path, limit: int, *, timeout: float = 30
) -> dict[str, object]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        str(launcher),
        "telemetry",
        "sweep",
        "--native-index",
        str(index),
        "--batch-size",
        str(limit),
        "--json",
        env=environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        process_group=0,
    )
    output = asyncio.create_task(read_output(process.stdout))
    errors = asyncio.create_task(read_output(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
    finally:
        # Kill only our observation group, including any stranded bd reader.
        # No executor or provider service belongs to this group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        draining = asyncio.gather(process.wait(), output, errors)
        while True:
            try:
                await asyncio.shield(draining)
                break
            except asyncio.CancelledError:
                continue
    stdout, output_truncated = output.result()
    stderr, error_truncated = errors.result()
    return {
        "code": "CollectorBatchFinished",
        "at": datetime.now(UTC).isoformat(),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "output": stdout,
        "error": stderr,
        "truncated": output_truncated or error_truncated,
    }


async def watch(launcher: Path, index: Path, limit: int, interval: int) -> None:
    descriptor = sys.stdout.fileno()
    blocking = os.get_blocking(descriptor)
    os.set_blocking(descriptor, False)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    previous = {value: signal.getsignal(value) for value in signals}
    for value in signals:
        loop.add_signal_handler(value, stopped.set)
    stop = asyncio.create_task(stopped.wait())
    try:
        while not stopped.is_set():
            running = asyncio.create_task(batch(launcher, index, limit))
            try:
                await asyncio.wait((running, stop), return_when=asyncio.FIRST_COMPLETED)
                if stopped.is_set():
                    break
                result: dict[str, object]
                try:
                    result = running.result()
                except (OSError, ValueError) as error:
                    result = {"code": "CollectorBatchFailed", "detail": str(error)}
                await send(descriptor, result, stop)
            finally:
                if not running.done():
                    running.cancel()
                try:
                    await running
                except (asyncio.CancelledError, OSError, ValueError):
                    pass
            try:
                await asyncio.wait_for(stopped.wait(), timeout=interval)
            except TimeoutError:
                pass
    finally:
        stop.cancel()
        os.set_blocking(descriptor, blocking)
        for value in signals:
            loop.remove_signal_handler(value)
            signal.signal(value, previous[value])


def run(state: Path, index: Path, limit: int, interval: int) -> dict[str, object]:
    launcher = Path(os.environ["HIVE_REPOSITORY_DIRECTORY"]) / "scripts/hive.py"
    with file_lock(state / "collection-watch.lock", timeout=0):
        asyncio.run(watch(launcher, index, limit, interval))
    return {"code": "CollectorStopped"}
