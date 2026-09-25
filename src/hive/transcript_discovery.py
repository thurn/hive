"""Probe both native hosts; UUID versions are never a routing contract."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hive.claude_store import regular
from hive.collection_registry import CollectionRegistry
from hive.errors import HiveError
from hive.identity import ThreadId
from hive.native_transcripts import NativeTranscripts
from hive.thread_links import thread_id
from hive.transcript_source import (
    Candidate,
    ClaudeSession,
    CodexTranscript,
    Rejected,
    ValidatedSource,
)


@dataclass(frozen=True)
class Found:
    source: Candidate | ValidatedSource
    error: str | None = None


Discovery = Found | Rejected


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
                cached = registry.cached_source(task)
                if cached is None:
                    results[task] = Rejected(native_error)
                else:
                    results[task] = Found(cached, native_error)
            elif task in paths:
                regular(paths[task])
                results[task] = Found(Candidate(CodexTranscript(paths[task])))
            elif matches:
                results[task] = Found(Candidate(ClaudeSession(matches[0].parent, task)))
            else:
                results[task] = Rejected("No native transcript for this task")
        except (OSError, ValueError, HiveError) as error:
            results[task] = Rejected(str(error))
    return results, native_error
