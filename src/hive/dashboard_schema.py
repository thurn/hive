"""Collector-owned feed materialization; dashboard requests are read-only."""

import re
import sqlite3

from hive.dashboard_values import rows
from hive.jsonvalue import string


def create(connection: sqlite3.Connection) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='card_summaries'"
        ).fetchone()
        is not None
    ):
        return
    for statement in (
        "CREATE TABLE bead_rows(bead TEXT PRIMARY KEY,project TEXT NOT NULL,status TEXT NOT NULL,title TEXT NOT NULL,updated TEXT NOT NULL,created TEXT NOT NULL,closed TEXT,assignee TEXT,blockers INTEGER NOT NULL,resolution TEXT,deferred_until TEXT,subtitle TEXT NOT NULL,payload TEXT NOT NULL,refreshed TEXT NOT NULL)",
        "CREATE TABLE card_ci(key TEXT NOT NULL,candidate TEXT NOT NULL,state TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(key,candidate))",
        "CREATE TABLE dashboard_garbage(thread TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(thread,generation))",
        "CREATE TABLE dashboard_card_dirty(key TEXT PRIMARY KEY)",
        "CREATE TABLE dashboard_diagnostic_dirty(thread TEXT PRIMARY KEY,revision INTEGER NOT NULL)",
        "CREATE TABLE dashboard_diagnostic_jobs(thread TEXT PRIMARY KEY,version INTEGER NOT NULL,revision INTEGER NOT NULL,phase TEXT NOT NULL,cursor TEXT NOT NULL,payload TEXT NOT NULL)",
        "CREATE TABLE dashboard_diagnostic_sets(thread TEXT PRIMARY KEY,payload TEXT NOT NULL)",
        "CREATE TABLE dashboard_duration_state(singleton INTEGER PRIMARY KEY,cursor INTEGER NOT NULL,complete INTEGER NOT NULL)",
        "INSERT INTO dashboard_duration_state VALUES (1,0,0)",
        "CREATE TABLE dashboard_duration_projects(thread TEXT PRIMARY KEY,cursor INTEGER NOT NULL)",
        "CREATE TABLE dashboard_duration_changes(thread TEXT NOT NULL,agent TEXT NOT NULL,call_id TEXT NOT NULL,PRIMARY KEY(thread,agent,call_id))",
        "CREATE TABLE dashboard_durations(thread TEXT NOT NULL,agent TEXT NOT NULL,call_id TEXT NOT NULL,project TEXT NOT NULL,kind TEXT NOT NULL,duration INTEGER NOT NULL,started TEXT NOT NULL,PRIMARY KEY(thread,agent,call_id))",
        "CREATE INDEX dashboard_durations_order ON dashboard_durations(project,kind,duration,started)",
        "CREATE INDEX dashboard_durations_time ON dashboard_durations(project,kind,started)",
        "CREATE TABLE dashboard_percentiles(project TEXT NOT NULL,kind TEXT NOT NULL,p95 INTEGER NOT NULL,checked REAL NOT NULL,PRIMARY KEY(project,kind))",
        "CREATE TABLE dashboard_ci_dirty(candidate TEXT PRIMARY KEY)",
        "INSERT INTO dashboard_ci_dirty SELECT candidate FROM tollgate_candidates",
        "CREATE TABLE dashboard_changes(thread TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(thread,response))",
        "CREATE TABLE dashboard_versions(thread TEXT PRIMARY KEY,version INTEGER NOT NULL)",
        "CREATE TABLE dashboard_serial(singleton INTEGER PRIMARY KEY,last INTEGER NOT NULL)",
        "INSERT INTO dashboard_serial VALUES (1,0)",
        "CREATE TABLE dashboard_active(thread TEXT PRIMARY KEY,generation INTEGER NOT NULL,unowned_key TEXT NOT NULL,version INTEGER NOT NULL)",
        "CREATE TABLE dashboard_runs(thread TEXT PRIMARY KEY,version INTEGER NOT NULL,cursor TEXT NOT NULL,total TEXT NOT NULL,unowned TEXT NOT NULL,publication INTEGER NOT NULL)",
        "CREATE TABLE dashboard_dirty(thread TEXT PRIMARY KEY)",
        "CREATE TABLE dashboard_bead_dirty(bead TEXT PRIMARY KEY)",
        "CREATE TABLE dashboard_health(singleton INTEGER PRIMARY KEY CHECK(singleton=1),source TEXT,refreshed TEXT,error TEXT,attribution TEXT)",
        "INSERT INTO dashboard_health VALUES (1,NULL,NULL,NULL,NULL)",
        "CREATE TABLE dashboard_stable_requests(response TEXT PRIMARY KEY,thread TEXT NOT NULL,observed TEXT NOT NULL,role TEXT NOT NULL,amount_picos TEXT,kind TEXT NOT NULL,shares TEXT NOT NULL,project TEXT NOT NULL,coverage INTEGER NOT NULL)",
        "CREATE INDEX dashboard_requests_time ON dashboard_stable_requests(observed)",
        "CREATE INDEX dashboard_requests_thread ON dashboard_stable_requests(thread)",
        "CREATE TABLE dashboard_contributions(thread TEXT NOT NULL,key TEXT NOT NULL,kind TEXT NOT NULL,project TEXT NOT NULL,amount_picos TEXT NOT NULL,unpriced INTEGER NOT NULL,coverage INTEGER NOT NULL,last_activity TEXT NOT NULL,roles TEXT NOT NULL,badges TEXT NOT NULL,PRIMARY KEY(thread,key))",
        "CREATE INDEX dashboard_contributions_key ON dashboard_contributions(key)",
        "CREATE TABLE card_summaries(key TEXT PRIMARY KEY,kind TEXT NOT NULL,project TEXT NOT NULL,amount_picos TEXT NOT NULL,unpriced INTEGER NOT NULL,coverage INTEGER NOT NULL,last_activity TEXT NOT NULL,roles TEXT NOT NULL,badges TEXT NOT NULL)",
        "CREATE INDEX card_summaries_activity ON card_summaries(last_activity DESC,key)",
        "CREATE INDEX responses_task_response ON responses(task,response)",
        "CREATE INDEX events_task_response ON claude_request_events(task,response)",
        "CREATE INDEX responses_observed ON responses(observed)",
        "INSERT INTO dashboard_dirty SELECT task FROM collection_tasks UNION SELECT task FROM responses UNION SELECT task FROM claude_request_events UNION SELECT thread FROM tool_calls UNION SELECT thread FROM session_events",
    ):
        connection.execute(statement)
    connection.execute(
        "CREATE TABLE dashboard_stage_requests AS SELECT *,0 AS generation FROM dashboard_stable_requests WHERE 0"
    )
    connection.execute(
        "CREATE UNIQUE INDEX dashboard_stage_requests_response ON dashboard_stage_requests(thread,generation,response)"
    )
    connection.execute(
        "CREATE INDEX dashboard_stage_requests_thread ON dashboard_stage_requests(thread,generation,observed)"
    )
    connection.execute(
        "CREATE TABLE dashboard_stage_contributions AS SELECT * FROM dashboard_contributions WHERE 0"
    )
    connection.execute(
        "CREATE UNIQUE INDEX dashboard_stage_contributions_key ON dashboard_stage_contributions(thread,key)"
    )
    connection.execute(
        "CREATE VIEW dashboard_requests AS SELECT s.* FROM dashboard_stable_requests s WHERE NOT EXISTS (SELECT 1 FROM dashboard_active a WHERE a.thread=s.thread) UNION ALL SELECT s.response,s.thread,s.observed,s.role,s.amount_picos,s.kind,CASE WHEN s.kind='unowned' THEN json_set(s.shares,'$[0].key',a.unowned_key) ELSE s.shares END,s.project,s.coverage FROM dashboard_stage_requests s JOIN dashboard_active a ON a.thread=s.thread AND a.generation=s.generation"
    )
    # Invalidate only affected root sessions; triggers remain entirely inside the
    # disposable observation store and never touch native Beads or host state.
    for table, column in (
        ("responses", "task"),
        ("claude_request_events", "task"),
        ("tool_calls", "thread"),
        ("session_events", "thread"),
        ("role_spans", "thread"),
        ("project_sessions", "thread"),
        ("sources", "task"),
        ("tollgate_mentions", "thread"),
    ):
        columns = [
            string(v["name"], "column")
            for v in rows(connection, f"PRAGMA table_info({table})")
        ]
        ignored = (
            {"scanned", "mtime_ns", "size"}
            if table == "sources"
            else {"updated"} if table == "project_sessions" else set()
        )
        changes = " OR ".join(
            f"NEW.{c} IS NOT OLD.{c}" for c in columns if c not in ignored
        )
        for operation in ("INSERT", "UPDATE", "DELETE"):
            condition = f" WHEN {changes}" if operation == "UPDATE" else ""
            reference = "OLD" if operation == "DELETE" else "NEW"
            if table == "project_sessions":
                eligible = f"({reference}.project IS NOT NULL OR EXISTS (SELECT 1 FROM collection_tasks WHERE task={reference}.thread))"
                condition = (
                    f" WHEN ({changes}) AND {eligible}"
                    if operation == "UPDATE"
                    else f" WHEN {eligible}"
                )
            connection.execute(
                f"CREATE TRIGGER dashboard_{table}_{operation.lower()} AFTER {operation} ON {table}{condition} BEGIN INSERT INTO dashboard_dirty SELECT {reference}.{column} WHERE NOT EXISTS (SELECT 1 FROM dashboard_dirty WHERE thread={reference}.{column}); END"
            )
    for operation in ("INSERT", "UPDATE", "DELETE"):
        ref = "OLD" if operation == "DELETE" else "NEW"
        connection.execute(
            f"CREATE TRIGGER dashboard_prices_{operation.lower()} AFTER {operation} ON response_estimates BEGIN INSERT INTO dashboard_dirty SELECT task FROM responses WHERE response={ref}.response UNION SELECT task FROM claude_request_events WHERE response={ref}.response EXCEPT SELECT thread FROM dashboard_dirty; END"
        )
        connection.execute(
            f"CREATE TRIGGER dashboard_intervals_{operation.lower()} AFTER {operation} ON bead_intervals BEGIN INSERT INTO dashboard_dirty SELECT COALESCE((SELECT root FROM codex_roots WHERE thread={ref}.thread),{ref}.thread) EXCEPT SELECT thread FROM dashboard_dirty; INSERT INTO dashboard_bead_dirty SELECT {ref}.bead WHERE NOT EXISTS (SELECT 1 FROM dashboard_bead_dirty WHERE bead={ref}.bead); END"
        )
        replay_change = (
            " WHEN NEW.status IS NOT OLD.status OR NEW.deleted IS NOT OLD.deleted OR NEW.renamed_to IS NOT OLD.renamed_to OR NEW.fingerprint IS NOT OLD.fingerprint"
            if operation == "UPDATE"
            else ""
        )
        connection.execute(
            f"CREATE TRIGGER dashboard_replays_{operation.lower()} AFTER {operation} ON bead_replays{replay_change} BEGIN INSERT INTO dashboard_dirty SELECT COALESCE(r.root,o.thread) FROM bead_seen_owners o LEFT JOIN codex_roots r ON r.thread=o.thread WHERE o.bead={ref}.bead EXCEPT SELECT thread FROM dashboard_dirty; INSERT INTO dashboard_bead_dirty SELECT {ref}.bead WHERE NOT EXISTS (SELECT 1 FROM dashboard_bead_dirty WHERE bead={ref}.bead); END"
        )
    connection.execute(
        "CREATE TRIGGER dashboard_ci AFTER UPDATE ON tollgate_candidates WHEN NEW.payload<>OLD.payload BEGIN INSERT INTO dashboard_dirty SELECT thread FROM tollgate_mentions WHERE candidate=NEW.candidate EXCEPT SELECT thread FROM dashboard_dirty; INSERT INTO dashboard_bead_dirty SELECT bead FROM tollgate_branch_matches WHERE candidate=NEW.candidate EXCEPT SELECT bead FROM dashboard_bead_dirty; END"
    )
    connection.execute(
        "CREATE TRIGGER dashboard_ci_new AFTER INSERT ON tollgate_candidates BEGIN INSERT INTO dashboard_dirty SELECT thread FROM tollgate_mentions WHERE candidate=NEW.candidate EXCEPT SELECT thread FROM dashboard_dirty; INSERT INTO dashboard_bead_dirty SELECT bead FROM tollgate_branch_matches WHERE candidate=NEW.candidate EXCEPT SELECT bead FROM dashboard_bead_dirty; END"
    )

    for table in (
        "tollgate_candidates",
        "tollgate_mentions",
        "tollgate_branch_matches",
        "tollgate_promotions",
    ):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            ref = "OLD" if operation == "DELETE" else "NEW"
            condition = (
                " WHEN NEW.payload IS NOT OLD.payload"
                if table == "tollgate_candidates" and operation == "UPDATE"
                else ""
            )
            connection.execute(
                f"CREATE TRIGGER dashboard_queue_{table}_{operation.lower()} AFTER {operation} ON {table}{condition} BEGIN INSERT INTO dashboard_ci_dirty SELECT {ref}.candidate WHERE NOT EXISTS (SELECT 1 FROM dashboard_ci_dirty WHERE candidate={ref}.candidate); END"
            )
    # Track invalidations independently of the queue so an in-flight paged
    # rebuild restarts after ownership or request evidence changes.
    for trigger in rows(
        connection,
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'dashboard_%'",
    ):
        sql = string(trigger["sql"], "trigger")
        additions = []
        for match in re.finditer(r"INSERT INTO dashboard_dirty (SELECT .*?);", sql):
            selected = (
                match.group(1)
                .split(" EXCEPT SELECT thread FROM dashboard_dirty", 1)[0]
                .split(" WHERE NOT EXISTS (SELECT 1 FROM dashboard_dirty", 1)[0]
            )
            additions.append(
                f"INSERT INTO dashboard_versions SELECT *,1 FROM ({selected}) WHERE 1 ON CONFLICT(thread) DO UPDATE SET version=version+1;"
            )
        structural = any(
            name in string(trigger["name"], "trigger name")
            for name in ("intervals_", "replays_", "role_spans_", "project_sessions_")
        )
        if additions and structural:
            connection.execute(
                "DROP TRIGGER " + string(trigger["name"], "trigger name")
            )
            connection.execute(sql.rsplit("END", 1)[0] + " ".join(additions) + " END")

    for table in ("responses", "claude_request_events"):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            ref = "OLD" if operation == "DELETE" else "NEW"
            connection.execute(
                f"CREATE TRIGGER dashboard_change_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN INSERT INTO dashboard_changes SELECT {ref}.task,{ref}.response WHERE NOT EXISTS (SELECT 1 FROM dashboard_changes WHERE thread={ref}.task AND response={ref}.response); END"
            )
    for operation in ("INSERT", "UPDATE", "DELETE"):
        ref = "OLD" if operation == "DELETE" else "NEW"
        connection.execute(
            f"CREATE TRIGGER dashboard_change_prices_{operation.lower()} AFTER {operation} ON response_estimates BEGIN INSERT INTO dashboard_changes SELECT task,response FROM responses WHERE response={ref}.response UNION SELECT task,response FROM claude_request_events WHERE response={ref}.response EXCEPT SELECT thread,response FROM dashboard_changes; END"
        )

    for table, column in (
        ("tool_calls", "thread"),
        ("session_events", "thread"),
        ("role_spans", "thread"),
    ):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            ref = "OLD" if operation == "DELETE" else "NEW"
            connection.execute(
                f"CREATE TRIGGER dashboard_diagnostics_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN INSERT INTO dashboard_diagnostic_dirty VALUES ({ref}.{column},1) ON CONFLICT(thread) DO UPDATE SET revision=revision+1; END"
            )

    for operation in ("INSERT", "UPDATE", "DELETE"):
        ref = "OLD" if operation == "DELETE" else "NEW"
        connection.execute(
            f"CREATE TRIGGER dashboard_duration_{operation.lower()} AFTER {operation} ON tool_calls BEGIN INSERT INTO dashboard_duration_changes SELECT {ref}.thread,{ref}.agent,{ref}.call_id WHERE NOT EXISTS (SELECT 1 FROM dashboard_duration_changes WHERE thread={ref}.thread AND agent={ref}.agent AND call_id={ref}.call_id); UPDATE dashboard_duration_state SET complete=0 WHERE singleton=1; END"
        )
    connection.execute(
        "INSERT INTO dashboard_diagnostic_dirty SELECT thread,1 FROM tool_calls UNION SELECT thread,1 FROM session_events"
    )

    for table, condition, selected in (
        (
            "project_sessions",
            "NEW.project IS NOT OLD.project OR NEW.cwd IS NOT OLD.cwd",
            "SELECT NEW.thread",
        ),
        (
            "cwd_projects",
            "NEW.project IS NOT OLD.project",
            "SELECT thread FROM project_sessions WHERE cwd=NEW.cwd",
        ),
    ):
        connection.execute(
            f"CREATE TRIGGER dashboard_duration_project_{table} AFTER UPDATE ON {table} WHEN {condition} BEGIN INSERT INTO dashboard_duration_projects SELECT *,0 FROM ({selected}) WHERE 1 ON CONFLICT(thread) DO UPDATE SET cursor=0; UPDATE dashboard_duration_state SET complete=0 WHERE singleton=1; INSERT INTO dashboard_diagnostic_dirty SELECT *,1 FROM ({selected}) WHERE 1 ON CONFLICT(thread) DO UPDATE SET revision=revision+1; END"
        )
