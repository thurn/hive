"""Indexed duration facts are warmed incrementally before percentile classification."""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

from hive.dashboard_values import rows
from hive.jsonvalue import integer, string
from hive.usage_store import UsageStore

PAGE = 128


def apply(connection: sqlite3.Connection, thread: str, agent: str, call: str) -> None:
    old = rows(
        connection,
        "SELECT project,kind FROM dashboard_durations WHERE thread=? AND agent=? AND call_id=?",
        (thread, agent, call),
    )
    value = rows(
        connection,
        "SELECT c.*,COALESCE(p.project,w.project,'Other') AS project FROM tool_calls c LEFT JOIN project_sessions p ON p.thread=c.thread LEFT JOIN cwd_projects w ON w.cwd=p.cwd WHERE c.thread=? AND c.agent=? AND c.call_id=?",
        (thread, agent, call),
    )
    connection.execute(
        "DELETE FROM dashboard_durations WHERE thread=? AND agent=? AND call_id=?",
        (thread, agent, call),
    )
    affected = {
        (string(v["project"], "project"), string(v["kind"], "kind")) for v in old
    }
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    if (
        value
        and value[0]["duration_ms"] is not None
        and str(value[0]["started_at"]) >= cutoff
    ):
        v = value[0]
        group = string(v["project"], "project")
        kind = string(
            v["tool"] if v["command_kind"] == "other" else v["command_kind"], "kind"
        )
        connection.execute(
            "INSERT INTO dashboard_durations VALUES (?,?,?,?,?,?,?)",
            (thread, agent, call, group, kind, v["duration_ms"], v["started_at"]),
        )
        affected.add((group, kind))
    connection.executemany(
        "DELETE FROM dashboard_percentiles WHERE project=? AND kind=?", sorted(affected)
    )
    connection.execute(
        "DELETE FROM dashboard_duration_changes WHERE thread=? AND agent=? AND call_id=?",
        (thread, agent, call),
    )


def batch(connection: sqlite3.Connection) -> bool:
    projects = rows(
        connection,
        "SELECT thread,cursor FROM dashboard_duration_projects ORDER BY rowid LIMIT 1",
    )
    if projects:
        project = projects[0]
        values = rows(
            connection,
            "SELECT rowid AS ordinal,thread,agent,call_id FROM tool_calls WHERE thread=? AND rowid>? ORDER BY rowid LIMIT ?",
            (project["thread"], project["cursor"], PAGE),
        )
        for value in values:
            apply(
                connection,
                string(value["thread"], "thread"),
                string(value["agent"], "agent", empty=True),
                string(value["call_id"], "call"),
            )
        if len(values) == PAGE:
            connection.execute(
                "UPDATE dashboard_duration_projects SET cursor=? WHERE thread=?",
                (values[-1]["ordinal"], project["thread"]),
            )
        else:
            connection.execute(
                "DELETE FROM dashboard_duration_projects WHERE thread=?",
                (project["thread"],),
            )
        return False
    cursor = integer(
        rows(
            connection, "SELECT cursor FROM dashboard_duration_state WHERE singleton=1"
        )[0]["cursor"],
        "cursor",
    )
    values = rows(
        connection,
        "SELECT rowid AS ordinal,thread,agent,call_id FROM tool_calls WHERE rowid>? ORDER BY rowid LIMIT ?",
        (cursor, PAGE),
    )
    for value in values:
        apply(
            connection,
            string(value["thread"], "thread"),
            string(value["agent"], "agent", empty=True),
            string(value["call_id"], "call"),
        )
    if values:
        connection.execute(
            "UPDATE dashboard_duration_state SET cursor=?,complete=0 WHERE singleton=1",
            (values[-1]["ordinal"],),
        )
        return False
    changed = rows(
        connection,
        "SELECT * FROM dashboard_duration_changes ORDER BY rowid LIMIT ?",
        (PAGE,),
    )
    for value in changed:
        apply(
            connection,
            string(value["thread"], "thread"),
            string(value["agent"], "agent", empty=True),
            string(value["call_id"], "call"),
        )
    complete = len(changed) < PAGE
    connection.execute(
        "UPDATE dashboard_duration_state SET complete=? WHERE singleton=1",
        (int(complete),),
    )
    return complete


def refresh(store: UsageStore, deadline: float) -> None:
    while time.monotonic() < deadline - 0.005:
        try:
            with store.connect() as connection:
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 1000
                )
                complete = batch(connection)
                connection.set_progress_handler(None, 0)
            if complete:
                return
        except sqlite3.OperationalError as error:
            if str(error) != "interrupted":
                raise
            return
