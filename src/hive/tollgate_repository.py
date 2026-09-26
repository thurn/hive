"""Repository inclusion is independent of optional bead attribution."""

import json
import sqlite3
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import record, sequence, string
from hive.launch_context import LaunchContext
from hive.tollgate_records import TERMINAL, Candidate, decode, identifier, instant
from hive.usage_store import row


def create_coverage(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS tollgate_coverage(project TEXT PRIMARY KEY,repository TEXT NOT NULL,observed TEXT NOT NULL,payload TEXT NOT NULL)"
    )
    # Older refreshes only discovered bead-matched candidates. Force a native read.
    connection.execute("DELETE FROM tollgate_mapping_state")


def retain(
    connection: sqlite3.Connection, facts: Candidate, project: str, now: str
) -> None:
    connection.execute(
        "INSERT INTO tollgate_candidates VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(candidate) DO UPDATE SET state=excluded.state,terminal=excluded.terminal,updated=excluded.updated,observed=excluded.observed,payload=excluded.payload",
        (
            facts.id,
            facts.repository,
            project,
            facts.state,
            int(facts.state in TERMINAL),
            facts.updated_at,
            now,
            json.dumps(facts.json(), sort_keys=True),
        ),
    )


def repositories(
    connection: sqlite3.Connection, context: LaunchContext, raw: object, now: str
) -> None:
    configured = {p.repository.resolve(): p.id for p in context.projects}
    beads = [
        string(row(v, 1)[0], "bead")
        for v in sequence(
            connection.execute("SELECT bead FROM bead_snapshots").fetchall(), "beads"
        )
    ]
    # Caller owns the transaction: malformed snapshots preserve the prior mapping.
    connection.execute("DELETE FROM tollgate_repositories")
    for entry in sequence(raw, "Tollgate repositories"):
        repository = record(entry, "repository")
        state = record(repository.get("state"), "repository state")
        path = Path(string(state.get("path"), "repository path")).resolve()
        project = configured.get(path)
        if project is None:
            continue
        repo = identifier(state.get("id"))
        connection.execute(
            "INSERT INTO tollgate_repositories VALUES (?,?,?,?)",
            (project, repo, str(path), now),
        )
        discovered: set[str] = set()
        event_times: list[str] = []
        history_times: list[str] = []
        history_ids: set[str] = set()
        for raw_event in sequence(repository.get("history", []), "repository history"):
            event = record(raw_event, "history event")
            at = instant(event.get("created_at"))
            if at is not None:
                event_times.append(at)
            # These native events carry queue-item identity, not buildset identity.
            if event.get("kind") in {
                "candidate.created",
                "queue.item-updated",
                "promotion.completed",
            }:
                payload = record(event.get("payload"), "candidate identity")
                candidate = identifier(payload.get("id"))
                discovered.add(candidate)
                if event.get("kind") == "promotion.completed" and at is not None:
                    connection.execute(
                        "INSERT INTO tollgate_promotions VALUES (?,?) ON CONFLICT(candidate) DO UPDATE SET at=MIN(at,excluded.at)",
                        (candidate, at),
                    )
        for key in ("queue", "history_items", "checks"):
            for raw_candidate in sequence(repository.get(key, []), "repository items"):
                facts = decode(raw_candidate)
                if facts.repository != repo:
                    raise HiveError(
                        ErrorCode.INVALID_RECORD, "Mismatched Tollgate repository"
                    )
                retain(connection, facts, project, now)
                discovered.add(facts.id)
                if key == "history_items":
                    history_ids.add(facts.id)
                    if facts.submitted_at is not None:
                        history_times.append(facts.submitted_at)
                if facts.branch is not None:
                    for bead in beads:
                        if facts.branch == bead or facts.branch.startswith(bead + "-"):
                            connection.execute(
                                "INSERT OR IGNORE INTO tollgate_branch_matches VALUES (?,?)",
                                (facts.id, bead),
                            )
        connection.executemany(
            "INSERT OR IGNORE INTO tollgate_pending(candidate,repository) VALUES (?,?)",
            ((candidate, repo) for candidate in sorted(discovered)),
        )
        coverage = dict(
            source="tg repo list",
            complete=False,
            gaps=["native_history_bounded_no_exhaustive_cursor"],
            history_candidates=len(history_ids),
            discovered_candidates=len(discovered),
            history_submitted_start=min(history_times, default=None),
            history_submitted_end=max(history_times, default=None),
            event_start=min(event_times, default=None),
            event_end=max(event_times, default=None),
        )
        connection.execute(
            "INSERT INTO tollgate_coverage VALUES (?,?,?,?) ON CONFLICT(project) DO UPDATE SET repository=excluded.repository,observed=excluded.observed,payload=excluded.payload",
            (project, repo, now, json.dumps(coverage, sort_keys=True)),
        )
