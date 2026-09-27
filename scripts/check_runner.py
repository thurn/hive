"""Bounded suite scheduling with grouped logs and process-group cleanup."""

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class Running:
    command: Sequence[str]
    process: subprocess.Popen[bytes]
    log: BinaryIO
    started: float


def run_checks(
    commands: Sequence[Sequence[str]], root: Path, deadline: float, *, jobs: int = 1
) -> int:
    if jobs < 1:
        raise ValueError("Check concurrency must be positive")
    interrupted: int | None = None

    def interrupt(number: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = number

    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {number: signal.getsignal(number) for number in signals}
    for number in signals:
        signal.signal(number, interrupt)
    pending = iter(commands)
    running: list[Running] = []
    exhausted = False
    try:
        while running or not exhausted:
            if interrupted is not None:
                return 128 + interrupted
            if time.monotonic() >= deadline:
                print("Check deadline exceeded", flush=True)
                return 124
            while len(running) < jobs and not exhausted and interrupted is None:
                command = next(pending, None)
                if command is None:
                    exhausted = True
                    break
                log = tempfile.TemporaryFile()
                try:
                    process = subprocess.Popen(
                        command, cwd=root, process_group=0, stdout=log, stderr=log
                    )
                except BaseException:
                    log.close()
                    raise
                running.append(Running(command, process, log, time.monotonic()))
                print("Started: " + " ".join(command), flush=True)
            finished = [item for item in running if item.process.poll() is not None]
            for item in finished:
                result = item.process.wait()
                report(item)
                cleanup(item)
                running.remove(item)
                if result:
                    return result
            if running and not finished:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return 0
    finally:
        try:
            # Python suites may own further process groups. Let their finally
            # blocks (including nested schedulers) drain before forcing cleanup.
            groups = {item.process.pid for item in running}
            try:
                if running:
                    groups.update(descendant_groups(running))
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                print(f"Cannot inspect nested check processes: {error}", flush=True)
            for item in running:
                send(item, signal.SIGINT)
            cleanup_deadline = time.monotonic() + 3
            for item in running:
                try:
                    item.process.wait(
                        timeout=max(0, cleanup_deadline - time.monotonic())
                    )
                except subprocess.TimeoutExpired:
                    pass
            for group in groups:
                signal_group(group, signal.SIGKILL)
            for item in running:
                item.process.wait()
                report(item)
                item.log.close()
        finally:
            for number in signals:
                signal.signal(number, previous[number])


def report(item: Running) -> None:
    elapsed = time.monotonic() - item.started
    print(f"Finished ({elapsed:.3f}s): " + " ".join(item.command), flush=True)
    item.log.seek(0)
    while chunk := item.log.read(65536):
        print(chunk.decode(errors="replace"), end="", flush=True)


def descendant_groups(running: Sequence[Running]) -> set[int]:
    # Capture nested groups before interrupting their parents: after a parent
    # exits those children are reparented and cannot be found by ancestry.
    roots = {item.process.pid for item in running}
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid="],
        capture_output=True,
        text=True,
        check=True,
        timeout=2,
    )
    processes = [
        tuple(int(value) for value in line.split())
        for line in result.stdout.splitlines()
    ]
    descendants = set(roots)
    while True:
        children = {pid for pid, parent, _ in processes if parent in descendants}
        added = children - descendants
        if not added:
            break
        descendants.update(added)
    return roots | {
        group
        for pid, _, group in processes
        if pid in descendants and group > 0 and group != os.getpgrp()
    }


def signal_group(group: int, number: int) -> None:
    try:
        os.killpg(group, number)
    except ProcessLookupError:
        pass


def send(item: Running, number: int) -> None:
    signal_group(item.process.pid, number)


def cleanup(item: Running, *, report_output: bool = False) -> None:
    # Descendants may outlive the direct suite process, even on success.
    send(item, signal.SIGKILL)
    item.process.wait()
    if report_output:
        report(item)
    item.log.close()
