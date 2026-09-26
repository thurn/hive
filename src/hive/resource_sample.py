"""Explicit bounded host/gate sampling; no background collector or native writes."""

import re
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from hive.dashboard_process import run
from hive.errors import HiveError
from hive.jsonvalue import record, sequence
from hive.project_config import Project
from hive.tollgate_process import TollgateProcess


def now() -> str:
    return datetime.now(UTC).isoformat()


def scaled(value: str, unit: str) -> int:
    return int(float(value) * 1024 ** "BKMGT".index(unit))


def host() -> dict[str, object]:
    started = now()
    result: dict[str, object] = dict[str, object](
        start=started, host=socket.gethostname(), platform=sys.platform
    )
    try:
        if sys.platform == "darwin":
            output = run(
                ("/usr/bin/top", "-l", "2", "-s", "1", "-n", "0"),
                Path("/"),
                5,
                cap=65536,
            )
            text = output.content.decode()
            cpu = re.findall(
                r"CPU usage: ([\d.]+)% user, ([\d.]+)% sys, ([\d.]+)% idle", text
            )
            memory = re.findall(
                r"PhysMem: ([\d.]+)([BKMGT]) used .*?, ([\d.]+)([BKMGT]) unused", text
            )
            if output.truncated or len(cpu) != 2 or len(memory) != 2:
                raise ValueError("Host sample format unavailable")
            user, system, idle = cpu[-1]
            used, used_unit, free, free_unit = memory[-1]
            result.update(
                cpu_idle_percent=float(idle),
                cpu_user_percent=float(user),
                cpu_system_percent=float(system),
                memory_used_bytes=scaled(used, used_unit),
                memory_available_bytes=scaled(free, free_unit),
                cpu_scope="top final one-second interval within capture bounds",
                memory_scope="top physical memory used/unused; rounded provider units",
            )
            swap = run(
                ("/usr/sbin/sysctl", "-n", "vm.swapusage"), Path("/"), 1, cap=4096
            ).content.decode()
            matched = re.search(r"used = ([\d.]+)([BKMGT])", swap)
            result["swap_used_bytes"] = scaled(*matched.groups()) if matched else None
        elif sys.platform.startswith("linux"):

            def cpu_ticks() -> tuple[int, int]:
                values = [
                    int(v)
                    for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]
                ]
                return sum(values), values[3]

            first, idle_first = cpu_ticks()
            time.sleep(1)
            last, idle_last = cpu_ticks()
            memory_values = {
                line.split(":")[0]: int(line.split()[1]) * 1024
                for line in Path("/proc/meminfo").read_text().splitlines()
                if len(line.split()) >= 3 and line.split()[2] == "kB"
            }
            result.update(
                cpu_idle_percent=(
                    100 * (idle_last - idle_first) / (last - first)
                    if last > first
                    else None
                ),
                memory_used_bytes=memory_values["MemTotal"]
                - memory_values["MemAvailable"],
                memory_available_bytes=memory_values["MemAvailable"],
                swap_used_bytes=memory_values["SwapTotal"] - memory_values["SwapFree"],
                cpu_scope="proc/stat delta; iowait excluded from idle",
                memory_scope="MemTotal minus MemAvailable",
            )
        else:
            raise ValueError("Host sampling unsupported on this platform")
    except (
        HiveError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        UnicodeError,
    ) as error:
        result["error"] = str(error)
    result["end"] = now()
    for field in (
        "cpu_idle_percent",
        "memory_used_bytes",
        "memory_available_bytes",
        "swap_used_bytes",
    ):
        result.setdefault(field, None)
    return result


def sample(project: Project) -> dict[str, object]:
    observed_host = host()
    gate: dict[str, object] = dict[str, object](
        start=now(),
        scope="Native Tollgate repository snapshot; capacity and volume fields may be shared",
    )
    try:
        snapshots = sequence(
            TollgateProcess(project.repository, 5).read(("repo", "list")),
            "repositories",
        )
        selected = next(
            (
                record(v)
                for v in snapshots
                if record(record(v).get("state", {})).get("path")
                == str(project.repository)
            ),
            None,
        )
        if selected is None:
            raise ValueError("Configured project absent from Tollgate snapshot")
        state = record(selected.get("state", {}))
        resources = record(selected.get("resources", {}))
        gate.update(
            repository=state.get("id"),
            resources={
                k: resources.get(k)
                for k in (
                    "active_runs",
                    "queued_runs",
                    "max_buildsets",
                    "repository_concurrency",
                    "cpu_reserved",
                    "memory_reserved",
                    "authoritative_volume_available",
                )
            },
            candidates=[
                dict[str, object](
                    id=item.get("id"),
                    state=item.get("state"),
                    source_oid=record(item.get("source_oid") or {}).get("bytes"),
                    buildset_id=item.get("buildset_id"),
                )
                for raw in sequence(selected.get("queue", []), "queue")
                for item in [record(record(raw).get("item", {}))]
            ],
        )
    except (HiveError, ValueError) as error:
        gate["error"] = str(error)
    gate["end"] = now()
    return dict[str, object](
        code="ResourceSample",
        schema=1,
        project=project.id,
        host=observed_host,
        gate=gate,
        coverage="Point observations, not continuous history; nested queues require operation logs",
    )
