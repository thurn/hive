"""One fresh CLI subprocess per MCP call; cancellation affects clients only."""

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from hive.errors import HiveError
from hive.identity import CandidateId, ProjectId
from hive.jsonvalue import integer, parse, record, string


@dataclass(frozen=True)
class WaitArguments:
    candidate: CandidateId
    project: ProjectId
    timeout: int

    @classmethod
    def read(cls, value: object) -> "WaitArguments":
        data = record(value, "wait arguments")
        if set(data) - {"candidate", "project", "timeout_seconds"}:
            raise ValueError("Unknown wait argument")
        timeout = integer(data.get("timeout_seconds", 3600), "timeout", minimum=1)
        if timeout > 2**31 - 1:
            raise ValueError("Timeout exceeds supported seconds")
        candidate = string(data.get("candidate"), "candidate")
        project = string(data.get("project"), "project")
        for argument in (candidate, project):
            if "\x00" in argument:
                raise ValueError("Tool arguments cannot contain NUL")
            argument.encode("utf-8")
        return cls(CandidateId(candidate), ProjectId(project), timeout)


def tool_result(value: dict[str, object], failed: bool) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": json.dumps(value)}],
        "structuredContent": value,
        "isError": failed,
    }


def failure(code: str, detail: str) -> dict[str, object]:
    return tool_result({"code": code, "detail": detail, "uncertain": True}, True)


async def stop_client(process: asyncio.subprocess.Process) -> None:
    """Drain only this read-only wait group, even after repeated cancellation."""
    # Killing the direct CLI alone can leave its native wait alive. There are
    # no provider mutations to finish in this group; the service is elsewhere.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    draining = asyncio.create_task(process.communicate())
    while True:
        try:
            await asyncio.shield(draining)
            return
        except asyncio.CancelledError:
            # EOF or another cancellation must not abandon child reaping.
            continue


async def wait_for_delivery(
    launcher: Path, arguments: WaitArguments
) -> dict[str, object]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "HIVE_SELECTED_COMMIT",
            "HIVE_SELECTED_DIRECTORY",
            "HIVE_BEADS_DIRECTORY",
            "HIVE_STATE_DIRECTORY",
            "HIVE_MUTATION_GUARD_FD",
            "HIVE_REPOSITORY_DIRECTORY",
        }
    }
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(launcher),
            "delivery",
            "wait",
            arguments.candidate,
            "--project",
            arguments.project,
            "--timeout-seconds",
            str(arguments.timeout),
            "--json",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            process_group=0,
        )
    except (OSError, ValueError) as error:
        return failure("ProviderUnavailable", f"Cannot start Hive: {error}")
    try:
        # The CLI owns its provider deadline. This outer guard also covers
        # startup and final reads if a broken child cannot return normally.
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), arguments.timeout + 120
        )
    except asyncio.CancelledError:
        await stop_client(process)
        raise
    except TimeoutError:
        await stop_client(process)
        return failure(
            "DeliveryTimeout",
            f"{arguments.candidate}: CLI did not finish; inspect the retained candidate",
        )
    failed = process.returncode != 0
    try:
        value = record(parse((stderr if failed else stdout).decode()), "CLI result")
        string(value.get("code"), "CLI outcome")
    except (HiveError, UnicodeDecodeError) as error:
        return failure("InvalidRecord", f"Invalid Hive CLI result: {error}")
    return tool_result(value, failed)
