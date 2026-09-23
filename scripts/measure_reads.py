"""Profile reads in a disposable server; this is not full latency acceptance.

Run with PYTHONPATH=src:tests and python -P after scripts/prepare-check. The -P
flag prevents scripts/hive.py from shadowing the application package. Reuse the
integration fixtures so this never initializes or discovers production state.
"""

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from cli_fixture import ROOT, Cli, cli_fixture
from server_fixture import private_server
from test_bead_json import native
from test_source_selection import git

from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.errors import HiveError
from hive.identity import BeadId
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.model import Queued


def command(cli: Cli, *arguments: str) -> dict[str, object]:
    result = subprocess.run(
        [str(ROOT / "bin/hive"), *arguments, "--json"],
        env={**cli.environment, "HIVE_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return record(parse(result.stdout))


def measure(
    name: str, operation: Callable[[], object], clients: int, repetitions: int
) -> dict[str, object]:
    operation()  # Source preparation and one warm-up are outside warm samples.
    barrier: threading.Barrier = threading.Barrier(clients)

    def worker(_: int) -> list[tuple[float, str | None]]:
        samples: list[tuple[float, str | None]] = []
        barrier.wait(timeout=20)
        for _ in range(repetitions):
            start = time.perf_counter()
            error: str | None = None
            try:
                operation()
            except (HiveError, subprocess.SubprocessError, AssertionError) as failure:
                error = str(failure)
            samples.append(((time.perf_counter() - start) * 1000, error))
        return samples

    with ThreadPoolExecutor(max_workers=clients) as pool:
        samples = [
            sample for group in pool.map(worker, range(clients)) for sample in group
        ]
    values: list[float] = sorted(elapsed for elapsed, error in samples if error is None)
    errors = [error for _, error in samples if error is not None]

    def percentile(fraction: float) -> float | None:
        return (
            round(values[math.ceil(len(values) * fraction) - 1], 2) if values else None
        )

    return {
        "operation": name,
        "clients": clients,
        "samples": len(samples),
        "errors": errors,
        "successful_p50_ms": percentile(0.5),
        "successful_p95_ms": percentile(0.95),
        "elapsed_ms": [round(elapsed, 3) for elapsed, _ in samples],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unfinished", type=int, default=1000)
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 8])
    parser.add_argument("--samples-per-client", type=int, default=32)
    options = record(vars(parser.parse_args()))
    count = integer(options["unfinished"], "unfinished", minimum=1)
    clients = tuple(
        integer(value, "clients", minimum=1)
        for value in sequence(options["clients"], "clients")
    )
    repetitions = integer(options["samples_per_client"], "samples", minimum=1)
    with private_server() as (connection, _), cli_fixture(connection) as cli:
        rows: list[dict[str, object]] = []
        for index in range(count):
            row = native(Queued())
            del row["id"]  # Beads allocates every fixture ID natively.
            row.update(
                title=f"Read fixture {index}", dependencies=[], dependency_count=0
            )
            rows.append(row)
        seed = connection.directory / "read-fixture.jsonl"
        seed.write_text("".join(json.dumps(row) + "\n" for row in rows))
        process = BeadsProcess(connection, "read-profile", timeout=60)
        process.run(["import", str(seed)], mutation=True)
        tasks = sequence(command(cli, "status")["tasks"], "tasks")
        if len(tasks) != count:
            raise RuntimeError("Fixture import did not retain every unfinished bead")
        identifier = BeadId(string(record(tasks[0])["id"], "bead"))
        store = BeadsStore(process)
        operations: tuple[tuple[str, Callable[[], object]], ...] = (
            ("cli-source", lambda: command(cli, "source")),
            ("native-get", lambda: store.get(identifier)),
            ("cli-show", lambda: command(cli, "task", "show", identifier)),
            ("native-active-query", store.active_records),
            ("cli-ready", lambda: command(cli, "task", "ready", "--project", "search")),
        )
        result = {
            "checkout_commit": git(ROOT, "rev-parse", "HEAD"),
            "checkout_dirty": bool(git(ROOT, "status", "--porcelain")),
            "fixture_commit": command(cli, "source")["commit"],
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "load_average": list(os.getloadavg()),
            "python": sys.version,
            "bd": process.run(["version"]),
            "unfinished": count,
            "scope": "Warm read profile; no dependencies, history, collector or backup. Not acceptance.",
            "measurements": [
                measure(name, operation, concurrency, repetitions)
                for concurrency in clients
                for name, operation in operations
            ],
        }
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
