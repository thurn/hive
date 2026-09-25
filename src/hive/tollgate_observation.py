"""Bounded CLI polling with terminal retention and recoverable availability gaps."""

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, sequence, string
from hive.launch_context import LaunchContext
from hive.tollgate_process import TollgateProcess
from hive.tollgate_records import TERMINAL, decode, identifier, instant
from hive.usage_store import UsageStore, row


def status(connection: sqlite3.Connection) -> dict[str, object]:
    value: object = connection.execute(
        "SELECT refreshed,error,behind FROM tollgate_health WHERE singleton=1"
    ).fetchone()
    refreshed, error, behind = (None, None, 1) if value is None else row(value, 3)
    return dict(
        tollgate_refreshed=refreshed,
        tollgate_error=error,
        tollgate_unavailable=error == "tollgate_unavailable",
        tollgate_behind=bool(behind),
    )


def repositories(
    connection: sqlite3.Connection, context: LaunchContext, raw: object, now: str
) -> None:
    configured = {p.repository.resolve(): p.id for p in context.projects}
    found: list[tuple[str, str, str, str]] = []
    branch_items: list[tuple[str, str, str]] = []
    promotions: list[tuple[str, str]] = []
    beads = [
        string(row(v, 1)[0], "bead")
        for v in sequence(
            connection.execute("SELECT bead FROM bead_snapshots").fetchall(), "beads"
        )
    ]
    for entry in sequence(raw, "Tollgate repositories"):
        repository = record(entry, "repository")
        state = record(repository.get("state"), "repository state")
        path = Path(string(state.get("path"), "repository path")).resolve()
        project = configured.get(path)
        if project is None:
            continue
        repo = identifier(state.get("id"))
        found.append((project, repo, str(path), now))
        for entry_event in sequence(
            repository.get("history", []), "repository history"
        ):
            event = record(entry_event, "history event")
            if event.get("kind") == "promotion.completed":
                payload = record(event.get("payload"), "promotion identity")
                at = instant(event.get("created_at"))
                if at is not None:
                    promotions.append((identifier(payload.get("id")), at))
        for key in ("queue", "history_items", "checks"):
            for raw_candidate in sequence(repository.get(key, []), "repository items"):
                item = record(record(raw_candidate).get("item"), "candidate item")
                branch = record(item.get("metadata"), "metadata").get("branch")
                if not isinstance(branch, str):
                    continue
                for bead in beads:
                    if branch == bead or branch.startswith(bead + "-"):
                        branch_items.append((identifier(item.get("id")), repo, bead))
    # A failed/malformed refresh leaves the old mapping and rows intact.
    connection.execute("DELETE FROM tollgate_repositories")
    connection.executemany("INSERT INTO tollgate_repositories VALUES (?,?,?,?)", found)
    connection.executemany(
        "INSERT INTO tollgate_promotions VALUES (?,?) ON CONFLICT(candidate) DO UPDATE SET at=MIN(at,excluded.at)",
        promotions,
    )
    for candidate, repo, bead in branch_items:
        connection.execute(
            "INSERT OR IGNORE INTO tollgate_branch_matches VALUES (?,?)",
            (candidate, bead),
        )
        connection.execute(
            "INSERT OR IGNORE INTO tollgate_pending(candidate,repository) VALUES (?,?)",
            (candidate, repo),
        )


def poll(
    store: UsageStore, context: LaunchContext, deadline: float
) -> dict[str, object]:
    if time.monotonic() >= deadline:
        with store.connect(write=False) as connection:
            return status(connection)
    if not context.projects:
        with store.connect(write=False) as connection:
            return status(connection)
    now = datetime.now(UTC).isoformat()
    signature = json.dumps([(p.id, str(p.repository)) for p in context.projects])
    with store.connect(write=False) as connection:
        prior = status(connection)["tollgate_error"]
    error: str | None = prior if isinstance(prior, str) else None
    try:
        with store.connect(write=False) as connection:
            refreshed: object = connection.execute(
                "SELECT refreshed FROM tollgate_mapping_state WHERE singleton=1 AND signature=?",
                (signature,),
            ).fetchone()
            old = None if refreshed is None else row(refreshed, 1)[0]
        if (
            old is None
            or string(old, "refresh")
            < (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        ):
            raw = TollgateProcess(context.repository, deadline - time.monotonic()).read(
                ("repo", "list")
            )
            with store.connect() as connection:
                repositories(connection, context, raw, now)
                error = None
                connection.execute(
                    "INSERT INTO tollgate_mapping_state VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET signature=excluded.signature,refreshed=excluded.refreshed",
                    (signature, now),
                )
        with store.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tollgate_pending(candidate,repository) SELECT DISTINCT m.candidate,r.repository FROM tollgate_mentions m CROSS JOIN tollgate_repositories r LEFT JOIN project_sessions s ON s.thread=m.thread WHERE s.project IS NULL OR s.project=r.project"
            )
            pending: object = connection.execute(
                "SELECT p.candidate,p.repository,r.project FROM tollgate_pending p JOIN tollgate_repositories r ON p.repository=r.repository LEFT JOIN tollgate_candidates c ON c.candidate=p.candidate WHERE (c.candidate IS NULL OR (c.repository=p.repository AND c.terminal=0)) AND (p.checked IS NULL OR (p.checked<? AND COALESCE(p.error,'')<>'not_found') OR (p.error='not_found' AND p.checked<?)) ORDER BY p.checked,p.candidate,p.repository LIMIT 32",
                (
                    (datetime.now(UTC) - timedelta(seconds=15)).isoformat(),
                    (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                ),
            ).fetchall()
        for value in sequence(pending, "pending candidates"):
            if time.monotonic() >= deadline:
                break
            candidate, repo, project = (
                string(v, "candidate routing") for v in row(value, 3)
            )
            with store.connect(write=False) as connection:
                known: object = connection.execute(
                    "SELECT repository,terminal FROM tollgate_candidates WHERE candidate=?",
                    (candidate,),
                ).fetchone()
                if known is not None and (row(known, 2)[0] != repo or row(known, 2)[1]):
                    continue
            issue: str | None = None
            try:
                facts = decode(
                    TollgateProcess(
                        context.repository, deadline - time.monotonic()
                    ).read(("status", candidate), repo)
                )
                if facts.id != candidate or facts.repository != repo:
                    raise HiveError(
                        ErrorCode.INVALID_RECORD, "Mismatched Tollgate identity"
                    )
                error = None
                with store.connect() as connection:
                    connection.execute(
                        "INSERT INTO tollgate_candidates VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(candidate) DO UPDATE SET state=excluded.state,terminal=excluded.terminal,updated=excluded.updated,observed=excluded.observed,payload=excluded.payload",
                        (
                            candidate,
                            repo,
                            project,
                            facts.state,
                            int(facts.state in TERMINAL),
                            facts.updated_at,
                            now,
                            json.dumps(facts.json(), sort_keys=True),
                        ),
                    )
            except (HiveError, OSError) as failure:
                if (
                    isinstance(failure, HiveError)
                    and failure.detail == "tollgate_candidate_not_found"
                ):
                    issue = "not_found"
                else:
                    issue = (
                        "tollgate_unavailable"
                        if isinstance(failure, OSError)
                        or "tollgate_unavailable" in str(failure)
                        else "tollgate_gap"
                    )
                    error = issue
            with store.connect() as connection:
                connection.execute(
                    "UPDATE tollgate_pending SET checked=?,error=? WHERE candidate=? AND repository=?",
                    (now, issue, candidate, repo),
                )
    except (HiveError, OSError) as failure:
        error = (
            "tollgate_unavailable"
            if isinstance(failure, OSError) or "tollgate_unavailable" in str(failure)
            else "tollgate_gap"
        )
    with store.connect() as connection:
        outstanding = connection.execute(
            "SELECT 1 FROM tollgate_pending p LEFT JOIN tollgate_candidates c ON c.candidate=p.candidate WHERE (c.candidate IS NULL OR (c.repository=p.repository AND c.terminal=0)) AND (p.checked IS NULL OR (p.checked<? AND COALESCE(p.error,'')<>'not_found') OR (p.error='not_found' AND p.checked<?)) LIMIT 1",
            (
                (datetime.now(UTC) - timedelta(seconds=15)).isoformat(),
                (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            ),
        ).fetchone()
        unresolved: object = connection.execute(
            "SELECT p.error FROM tollgate_pending p LEFT JOIN tollgate_candidates c ON c.candidate=p.candidate WHERE p.error IN ('tollgate_gap','tollgate_unavailable') AND (c.candidate IS NULL OR (c.repository=p.repository AND c.terminal=0)) ORDER BY p.error DESC LIMIT 1"
        ).fetchone()
        if unresolved is not None:
            error = string(row(unresolved, 1)[0], "poll error")
        connection.execute(
            "UPDATE tollgate_health SET refreshed=?,error=?,behind=? WHERE singleton=1",
            (now, error, int(outstanding is not None)),
        )
        return status(connection)


def candidates(
    connection: sqlite3.Connection, candidate_id: str | None = None
) -> list[dict[str, object]]:
    from hive.bead_assignment import Evidence
    from hive.bead_requests import Request
    from hive.identity import Host, ThreadId
    from hive.transcript_source import ValidatedSource
    from hive.usage import timestamp

    evidence = Evidence.read(connection)
    values: object = connection.execute(
        "SELECT c.candidate,c.project,c.payload,MAX(c.updated,COALESCE(p.at,c.updated)) AS effective_update FROM tollgate_candidates c LEFT JOIN tollgate_promotions p ON c.candidate=p.candidate WHERE (? IS NULL OR c.candidate=?) ORDER BY effective_update DESC,c.candidate",
        (candidate_id, candidate_id),
    ).fetchall()
    result: list[dict[str, object]] = []
    for raw in sequence(values, "candidates"):
        candidate, project, payload, updated = row(raw, 4)
        value = record(parse(string(payload, "candidate metadata")))
        value["project"] = project
        value["updated_at"] = updated
        promotion: object = connection.execute(
            "SELECT at FROM tollgate_promotions WHERE candidate=?", (candidate,)
        ).fetchone()
        if promotion is not None:
            value["promoted_at"] = row(promotion, 1)[0]
        mentions: object = connection.execute(
            "SELECT m.thread,m.agent,m.first_seen,c.validated_source FROM tollgate_mentions m LEFT JOIN collection_tasks c ON c.task=m.thread WHERE m.candidate=? ORDER BY m.first_seen,m.thread,m.agent",
            (candidate,),
        ).fetchall()
        links: list[dict[str, object]] = []
        for mention in sequence(mentions, "mentions"):
            thread, agent, seen, source = row(mention, 4)
            owner = string(thread, "thread")
            child = string(agent, "agent", empty=True)
            request = Request(
                "candidate",
                ThreadId(owner),
                (
                    Host.CODEX
                    if source is None
                    else ValidatedSource.decode(
                        string(source, "validated source"), ThreadId(owner)
                    ).locator.host
                ),
                timestamp(seen),
                None,
                child or None,
                None,
                None,
                "tollgate",
                0,
            )
            assignment = evidence.assign(request)
            links.append(
                dict(
                    thread=owner,
                    agent=child,
                    first_seen=seen,
                    beads=[i.bead for i, _ in assignment.shares],
                    method="transcript",
                )
            )
        matched = any(link["beads"] for link in links)
        if not matched:
            branch_rows: object = connection.execute(
                "SELECT bead FROM tollgate_branch_matches WHERE candidate=? ORDER BY bead",
                (candidate,),
            ).fetchall()
            links.extend(
                dict(
                    thread=None,
                    agent=None,
                    first_seen=value["submitted_at"],
                    beads=[row(v, 1)[0]],
                    method="branch",
                )
                for v in sequence(branch_rows, "branch matches")
            )
        value["links"] = links
        result.append(value)
    return result
