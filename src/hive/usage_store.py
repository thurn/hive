"""Public observation API over host-neutral persistence and collection."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hive.identity import CodexTaskId, Host
from hive.jsonvalue import integer, parse, sequence, string
from hive.telemetry_store import TelemetryStore
from hive.telemetry_store import row as row
from hive.transcript_chunks import MAX_BATCH
from hive.usage import tokens


@dataclass(frozen=True)
class UsageStore(TelemetryStore):
    def collect(
        self,
        task: CodexTaskId,
        path: Path,
        *,
        budget: int = MAX_BATCH,
        from_start: bool = False,
        host: Host | None = None,
        agent: CodexTaskId | None = None,
    ) -> dict[str, object]:
        """Compatibility entry point; host selection belongs to the boundary."""
        from hive.transcript_collection import collect

        return collect(
            self,
            task,
            path,
            budget=budget,
            from_start=from_start,
            host=host,
            agent=agent,
        )

    def report(self, task: CodexTaskId) -> dict[str, object]:
        with self.connect(write=False) as connection:
            cursor = connection.execute(
                "SELECT input, cached, cache_write, output, reasoning, cache_write_1h FROM responses WHERE task = ?",
                (task,),
            )
            observed, known = 0, 0
            totals = [0] * 6
            # Bounded result batches and Python integers preserve exact totals
            # even when many valid native counters exceed SQLite's sum range.
            while True:
                fetched: object = cursor.fetchmany(512)
                batch = sequence(fetched, "usage batch")
                if not batch:
                    break
                for raw in batch:
                    values = row(raw, 6)
                    observed += 1
                    if all(value is None for value in values[:5]):
                        continue
                    for index, value in enumerate(values):
                        totals[index] += integer(value, "stored token counter")
                    known += 1
            counter_names = (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "cache_write_1h_input_tokens",
            )
            host = stored_host(connection, task)
            summed: dict[str, object] | None = None
            if known:
                summed = tokens(dict(zip(counter_names, totals, strict=True))).value()
                # Keep the Codex usage report shape stable during migration.
                if host != Host.CLAUDE:
                    summed.pop("cache_write_1h_input_tokens")
            return {
                "code": "ObservedUsage",
                "host": host,
                "by_agent": agent_usage(connection, task),
                "task": task,
                "observed_responses": observed,
                "responses_with_usage": known,
                "responses_missing_usage": observed - known,
                "known_tokens": summed,
                **source_status(connection, task),
                "api_equivalent_usd": None,
                "coverage": "Usage counters only; use hive cost --task for thread estimates or hive cost --bead for ownership-based attribution",
            }


def source_status(
    connection: sqlite3.Connection, task: CodexTaskId
) -> dict[str, object]:
    fetched: object = connection.execute(
        "SELECT file,scanned,remaining,incomplete,error FROM sources WHERE task=? ORDER BY file",
        (task,),
    ).fetchall()
    sources = [row(value, 5) for value in sequence(fetched, "source files")]
    errors = [
        string(value[4], "source error") for value in sources if value[4] is not None
    ]
    remaining = [
        integer(value[2], "remaining bytes")
        for value in sources
        if value[2] is not None
    ]
    gap_count: object = connection.execute(
        "SELECT COUNT(*) FROM gaps WHERE task = ?", (task,)
    ).fetchone()
    gaps = integer(row(gap_count, 1)[0], "gap count")
    recent: object = connection.execute(
        "SELECT position, detail FROM gaps WHERE task = ? ORDER BY rowid DESC LIMIT 20",
        (task,),
    ).fetchall()
    details: list[dict[str, object]] = []
    for raw in sequence(recent, "recent gaps"):
        offset, detail = row(raw, 2)
        details.append(
            {
                "offset": integer(offset, "gap offset"),
                "detail": string(detail, "gap detail"),
            }
        )
    return {
        "parse_gaps": gaps,
        "recent_gaps": details,
        "last_scan": max(
            (string(value[1], "scan time") for value in sources), default=None
        ),
        "remaining_bytes": (
            sum(remaining) if sources and len(remaining) == len(sources) else None
        ),
        "incomplete_tail": (
            any(bool(value[3]) for value in sources) if sources else None
        ),
        "source_error": "; ".join(errors) if errors else None,
        "source_files": [
            {"file": value[0], "remaining_bytes": value[2], "error": value[4]}
            for value in sources
        ],
    }


def stored_host(connection: sqlite3.Connection, task: CodexTaskId) -> Host | None:
    values: object = connection.execute(
        "SELECT DISTINCT host FROM sources WHERE task=? UNION SELECT 'claude' FROM claude_request_events WHERE task=?",
        (task, task),
    ).fetchall()
    hosts = {
        string(row(value, 1)[0], "source host")
        for value in sequence(values, "source hosts")
    }
    return Host(next(iter(hosts))) if len(hosts) == 1 else None


def agent_usage(
    connection: sqlite3.Connection, task: CodexTaskId
) -> list[dict[str, object]]:
    values: object = connection.execute(
        "SELECT agent,usage FROM responses WHERE task=?", (task,)
    ).fetchall()
    groups: dict[str | None, tuple[int, dict[str, int]]] = {}
    for value in sequence(values, "agent usage"):
        agent, raw_usage = row(value, 2)
        name = None if agent is None else string(agent, "agent")
        count, totals = groups.get(name, (0, {}))
        if raw_usage is not None:
            for key, counter in (
                tokens(parse(string(raw_usage, "usage"))).value().items()
            ):
                totals[key] = totals.get(key, 0) + integer(counter, key)
        groups[name] = count + 1, totals
    result: list[dict[str, object]] = []
    for name, (count, totals) in groups.items():
        result.append(
            {"agent": name, "responses": count, "known_tokens": totals or None}
        )
    return result
