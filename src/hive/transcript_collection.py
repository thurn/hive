"""Select native adapters at the public collection boundary."""

from pathlib import Path

from hive.claude_store import ClaudeFile, metadata
from hive.codex_folding import root
from hive.codex_store import CodexFile
from hive.identity import Host, ThreadId
from hive.telemetry_store import TelemetryStore
from hive.transcript_batch import Scanned, value
from hive.transcript_batch import collect as scan
from hive.transcript_chunks import MAX_BATCH


def collect(
    store: TelemetryStore,
    task: ThreadId,
    path: Path,
    *,
    budget: int = MAX_BATCH,
    from_start: bool = False,
    host: Host | None = None,
    agent: ThreadId | None = None,
) -> dict[str, object]:
    # Preserve manual collect's filename inference; sweeps supply known hosts.
    if host == Host.CLAUDE or (
        host is None
        and (path.name == f"{task}.jsonl" or path.parent.name == "subagents")
    ):
        claude = ClaudeFile(task, path)
        outcome = scan(store, claude, budget=budget, from_start=from_start)
        if isinstance(outcome, Scanned):
            metadata(store, task, path)
            with store.connect() as connection:
                from hive.diagnostic_roles import finalize

                finalize(connection, task)
        return {**value(task, outcome), "host": Host.CLAUDE, "file": claude.source.file}
    with store.connect() as connection:
        canonical = root(connection, task)
    if canonical != task:
        agent, task = task, canonical
    return value(
        task,
        scan(store, CodexFile(task, path, agent), budget=budget, from_start=from_start),
    )
