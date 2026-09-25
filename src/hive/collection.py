"""A bounded observation batch that never changes a work item or launches agents."""

import sqlite3
import time
from collections import deque
from pathlib import Path

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.claude_store import files, metadata
from hive.collection_registry import CollectionRegistry
from hive.errors import ErrorCode, HiveError
from hive.event_ingest import ingest
from hive.event_retention import retain as retain_events
from hive.identity import Host
from hive.jsonvalue import string
from hive.launch_context import LaunchContext
from hive.locking import file_lock
from hive.thread_links import read
from hive.transcript_discovery import probe
from hive.usage_store import UsageStore


def sweep(context: LaunchContext, index: Path, limit: int) -> dict[str, object]:
    if not 1 <= limit <= 64:
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Collection batch must contain 1 through 64 tasks"
        )
    deadline = time.monotonic() + 5
    usage = UsageStore(context.state / "telemetry.sqlite3")
    registry = CollectionRegistry(usage)
    with file_lock(context.state / "collection.lock", timeout=0):
        links = None
        gaps = None
        registry_error: str | None = None
        try:
            links, gaps = read(
                BeadsProcess(BeadsConnection.read(context.beads), timeout=2)
            )
        except (HiveError, OSError) as error:
            registry_error = str(error)
        registry.refresh(links, gaps, registry_error)
        event_health = retain_events(
            usage,
            context.state,
            min(deadline, time.monotonic() + 0.25),
            permitted=registry_error is None,
        )
        event_health.update(ingest(usage, context.state, deadline))
        selected = registry.next(limit)
        discoveries, native_error = probe(
            selected, index, context.claude_projects, registry
        )
        work: dict[str, tuple[Path, ...]] = {}
        errors: dict[str, list[str]] = {}
        file_results: dict[str, list[dict[str, object]]] = {}
        for task in selected:
            found = discoveries[task]
            try:
                work[task] = (
                    ()
                    if found.path is None
                    else (
                        files(usage, task, found.path)
                        if found.host == Host.CLAUDE
                        else (found.path,)
                    )
                )
            except (HiveError, OSError, ValueError, sqlite3.Error) as error:
                work[task] = ()
                errors[task] = [str(error)]
        # One file per thread per round; older attempts sort before fresh ones.
        queue = deque(selected)
        results: dict[str, dict[str, object]] = {}
        while queue and time.monotonic() < deadline:
            task = queue.popleft()
            found = discoveries[task]
            candidates = work[task]
            problem = found.error
            validated_path: Path | None = None
            collected_host = found.host
            try:
                if not candidates or found.path is None or found.host is None:
                    result: dict[str, object] = {
                        "task": task,
                        "error": problem or "No readable transcript files",
                    }
                else:
                    path, *rest = candidates
                    result = usage.collect(task, path, host=found.host)
                    result["discovery_error"] = problem
                    if result["error"] is not None:
                        problem = string(result["error"], "collection error")
                    else:
                        validated_path = found.path
                    if found.host == Host.CLAUDE:
                        metadata(usage, task, path)
                    work[task] = tuple(rest)
                    if rest:
                        queue.append(task)
                if result["error"] is not None:
                    problem = string(result["error"], "collection error")
                file_results.setdefault(task, []).append(result)
                results[task] = result
            except (HiveError, OSError, ValueError, sqlite3.Error) as error:
                problem = str(error)
                results[task] = {"task": task, "error": problem}
            problems = errors.setdefault(task, [])
            if problem is not None and problem not in problems:
                problems.append(problem)
            combined = "; ".join(problems) if problems else None
            results[task] = {
                **results[task],
                "error": combined,
                "files": file_results.get(task, []),
            }
            registry.attempted(task, combined, validated_path, collected_host)
        return {
            "code": "CollectionBatch",
            **event_health,
            "source": context.commit,
            "registry_error": registry_error,
            "native_index_error": native_error,
            "attempted": len(results),
            "results": list(results.values()),
        }
