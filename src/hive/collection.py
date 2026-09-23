"""A bounded observation batch that never changes a work item or launches agents."""

import sqlite3
import time
from pathlib import Path

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection_registry import CollectionRegistry
from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId
from hive.jsonvalue import string
from hive.launch_context import LaunchContext
from hive.locking import file_lock
from hive.native_transcripts import NativeTranscripts
from hive.thread_links import read
from hive.usage_store import UsageStore


def sweep(context: LaunchContext, index: Path, limit: int) -> dict[str, object]:
    if not 1 <= limit <= 64:
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Collection batch must contain 1 through 64 tasks"
        )
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
        selected = registry.next(limit)
        paths: dict[CodexTaskId, Path] = {}
        native_error: str | None = None
        try:
            paths = NativeTranscripts(index).find(selected)
        except (HiveError, OSError, ValueError, sqlite3.Error) as error:
            native_error = str(error)
        deadline = time.monotonic() + 5
        results: list[dict[str, object]] = []
        for task in selected:
            if time.monotonic() >= deadline:
                break
            problem = native_error
            validated_path: Path | None = None
            try:
                path = paths.get(task)
                if path is None:
                    problem = (
                        native_error or "Native index has no transcript for this task"
                    )
                    path = registry.cached_path(task)
                if path is None:
                    result: dict[str, object] = {"task": task, "error": problem}
                else:
                    result = usage.collect(task, path)
                    result["discovery_error"] = problem
                    if result["error"] is not None:
                        problem = string(result["error"], "collection error")
                    else:
                        validated_path = path
                results.append(result)
            except (HiveError, OSError, ValueError, sqlite3.Error) as error:
                problem = str(error)
                results.append({"task": task, "error": problem})
            registry.attempted(task, problem, validated_path)
        return {
            "code": "CollectionBatch",
            "source": context.commit,
            "registry_error": registry_error,
            "native_index_error": native_error,
            "attempted": len(results),
            "results": results,
        }
