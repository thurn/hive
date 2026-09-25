"""Fair per-file transcript collection inside the sweep's time allowance."""

import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from hive.claude_store import files
from hive.collection_registry import CollectionRegistry
from hive.errors import HiveError
from hive.identity import ThreadId
from hive.jsonvalue import sequence, string
from hive.transcript_discovery import probe
from hive.transcript_source import (
    ClaudeSession,
    Collected,
    Rejected,
    Unchanged,
    ValidatedSource,
)
from hive.usage_store import UsageStore, row


@dataclass(frozen=True)
class SourceFile:
    path: Path
    agent: ThreadId | None = None


def collect(
    usage: UsageStore,
    registry: CollectionRegistry,
    selected: tuple[ThreadId, ...],
    index: Path,
    projects: Path,
    deadline: float,
) -> tuple[dict[str, dict[str, object]], str | None]:
    discoveries, native_error = probe(selected, index, projects, registry)
    work: dict[str, tuple[SourceFile, ...]] = {}
    errors: dict[str, list[str]] = {}
    file_results: dict[str, list[dict[str, object]]] = {}
    for task in selected:
        found = discoveries[task]
        try:
            if isinstance(found, Rejected):
                work[task] = ()
                continue
            locator = found.source.locator
            if isinstance(locator, ClaudeSession):
                work[task] = tuple(
                    SourceFile(p) for p in files(usage, task, locator.main)
                )
            else:
                with usage.connect(write=False) as connection:
                    children: object = connection.execute(
                        "SELECT r.thread,p.path FROM codex_roots r JOIN project_sessions p ON r.thread=p.thread WHERE r.root=? AND r.thread<>r.root ORDER BY r.thread",
                        (task,),
                    ).fetchall()
                work[task] = (SourceFile(locator.path),) + tuple(
                    SourceFile(
                        Path(string(row(v, 2)[1], "child path")),
                        ThreadId(string(row(v, 2)[0], "child")),
                    )
                    for v in sequence(children, "child files")
                )
        except (HiveError, OSError, ValueError, sqlite3.Error) as error:
            work[task] = ()
            errors[task] = [str(error)]
    queue = deque(selected)
    results: dict[str, dict[str, object]] = {}
    while queue and time.monotonic() < deadline:
        task = queue.popleft()
        found = discoveries[task]
        candidates = work[task]
        problem = found.error
        validated: ValidatedSource | None = None
        try:
            if not candidates or isinstance(found, Rejected):
                result: dict[str, object] = {
                    "task": task,
                    "error": problem or "No readable transcript files",
                }
            else:
                source, *rest = candidates
                result = usage.collect(
                    task,
                    source.path,
                    host=found.source.locator.host,
                    agent=source.agent,
                )
                result["discovery_error"] = problem
                if result["error"] is not None:
                    problem = string(result["error"], "collection error")
                else:
                    # A Codex child validates itself, not the root transcript.
                    # A Claude child establishes the session directory identity.
                    if (
                        isinstance(found.source.locator, ClaudeSession)
                        or source.agent is None
                    ):
                        validated = ValidatedSource(found.source.locator)
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
        if validated is not None:
            registry.attempted(task, Collected(validated, combined))
        elif isinstance(found, Rejected):
            registry.attempted(task, Rejected(combined or found.error))
        else:
            registry.attempted(task, Unchanged(combined))
    return results, native_error
