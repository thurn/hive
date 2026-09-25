"""A bounded observation batch that never changes a work item or launches agents."""

import time
from pathlib import Path

from hive.bead_history import refresh as refresh_history
from hive.bead_history import status as history_status
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection_pass import collect
from hive.collection_registry import CollectionRegistry
from hive.collection_schedule import select
from hive.errors import ErrorCode, HiveError
from hive.event_ingest import ingest
from hive.event_retention import retain as retain_events
from hive.launch_context import LaunchContext
from hive.locking import file_lock
from hive.project_discovery import discover
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
        with usage.connect() as connection:
            previous = history_status(connection)
            changed = select(
                connection, limit, idle=False, deadline=time.monotonic() + 0.1
            )
        pending_events = (
            next((context.state / "otlp-spool").glob("*.json"), None) is not None
        )
        # Keep one second of transcript progress even during history backlogs.
        transcript_deadline = min(
            deadline - 0.8,
            time.monotonic()
            + (
                1
                if previous["bead_events_caught_up"] is not True or pending_events
                else 4.2
            ),
        )
        results, native_error = collect(
            usage,
            registry,
            changed,
            index,
            context.claude_projects,
            transcript_deadline,
        )
        links = None
        gaps = None
        registry_error: str | None = None
        try:
            links, gaps = refresh_history(
                usage,
                BeadsProcess(BeadsConnection.read(context.beads), timeout=2),
                min(deadline, time.monotonic() + 2),
            )
        except (HiveError, OSError) as error:
            registry_error = str(error)
            with usage.connect() as connection:
                connection.execute(
                    "UPDATE bead_event_cursor SET caught_up=0,error=? WHERE singleton=1",
                    (registry_error,),
                )
        event_health = ingest(usage, context.state, min(deadline, time.monotonic() + 2))
        linked = tuple(sorted({link.task for link in links or ()}))
        discovery = discover(
            usage, context, index, linked, min(deadline, time.monotonic() + 0.3)
        )
        from hive.tollgate_observation import poll as poll_tollgate

        tollgate = poll_tollgate(usage, context, min(deadline, time.monotonic() + 0.3))
        registry.refresh(links, gaps, registry_error)
        from hive.dashboard_summary import refresh as refresh_summaries

        summaries = refresh_summaries(
            usage, context, min(deadline, time.monotonic() + 0.2)
        )
        with usage.connect(write=False) as connection:
            bead_health = history_status(connection)
        event_health.update(
            retain_events(
                usage,
                context.state,
                min(deadline, time.monotonic() + 0.25),
                permitted=registry_error is None
                and bead_health["bead_events_caught_up"] is True
                and discovery["discovery_error"] is None
                and discovery["discovery_behind"] is False,
            )
        )
        if time.monotonic() < deadline and len(results) < limit:
            selected = tuple(t for t in registry.next(limit) if t not in results)[
                : limit - len(results)
            ]
            idle_results, error = collect(
                usage, registry, selected, index, context.claude_projects, deadline
            )
            results.update(idle_results)
            native_error = native_error or error
        with usage.connect() as connection:
            from hive.diagnostic_store import orphan

            orphan(connection)
        return {
            "code": "CollectionBatch",
            **event_health,
            **bead_health,
            **discovery,
            **tollgate,
            **summaries,
            "source": context.commit,
            "registry_error": registry_error,
            "native_index_error": native_error,
            "attempted": len(results),
            "results": list(results.values()),
        }
