"""Probe both native hosts; UUID versions are never a routing contract."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hive.claude_store import regular
from hive.collection_registry import CollectionRegistry
from hive.errors import HiveError
from hive.identity import Host, ThreadId
from hive.native_transcripts import NativeTranscripts
from hive.thread_links import thread_id


@dataclass(frozen=True)
class Discovery:
    host: Host | None
    path: Path | None
    error: str | None


def probe(
    tasks: tuple[ThreadId, ...],
    index: Path,
    projects: Path,
    registry: CollectionRegistry,
) -> tuple[dict[ThreadId, Discovery], str | None]:
    paths: dict[ThreadId, Path] = {}
    native_error: str | None = None
    try:
        paths = NativeTranscripts(index).find(tasks)
    except (HiveError, OSError, ValueError, sqlite3.Error) as error:
        native_error = str(error)
    results: dict[ThreadId, Discovery] = {}
    for task in tasks:
        try:
            if thread_id(task) is None:
                raise ValueError("Invalid thread ID for transcript discovery")
            matches = list(projects.glob(f"*/{task}.jsonl"))
            for path in matches:
                regular(path, parents=1)
            if len(matches) > 1:
                raise ValueError("Claude session matches multiple project directories")
            if paths.get(task) is not None and matches:
                raise ValueError(
                    "Thread matches both Codex and Claude; collection refused"
                )
            if native_error is not None:
                host = registry.cached_host(task)
                path = registry.cached_path(task) if host is not None else None
                if path is not None:
                    regular(path)
                results[task] = Discovery(host, path, native_error)
            elif task in paths:
                regular(paths[task])
                results[task] = Discovery(Host.CODEX, paths[task], None)
            elif matches:
                results[task] = Discovery(Host.CLAUDE, matches[0], None)
            else:
                results[task] = Discovery(
                    None, None, "No native transcript for this task"
                )
        except (OSError, ValueError, HiveError) as error:
            results[task] = Discovery(None, None, str(error))
    return results, native_error
