"""One root owns each Codex response; native child identity stays observable."""

import hashlib
import sqlite3

from hive.identity import ThreadId
from hive.jsonvalue import sequence, string
from hive.usage_store import row


def root(connection: sqlite3.Connection, thread: str) -> ThreadId:
    value: object = connection.execute(
        "SELECT root FROM codex_roots WHERE thread=?", (thread,)
    ).fetchone()
    return ThreadId(thread if value is None else string(row(value, 1)[0], "root"))


def fold(connection: sqlite3.Connection, child: str, parent: str) -> None:
    if child == parent:
        return
    # Diagnostics collected before native parent metadata arrived follow the same
    # root/file identity without requiring access to an old transcript again.
    for table in (
        "tool_calls",
        "tool_commands",
        "role_spans",
        "diagnostic_sessions",
        "diagnostic_titles",
    ):
        fields = [
            string(row(v, 6)[1], "column")
            for v in sequence(
                connection.execute(f"PRAGMA table_info({table})").fetchall(), "columns"
            )
        ]
        if not fields:
            continue
        expressions = [
            (
                "?"
                if name == "thread"
                else (
                    "CASE WHEN agent='' THEN ? ELSE agent END"
                    if name == "agent"
                    else (
                        "CASE WHEN file='' THEN ? ELSE file END"
                        if name == "file"
                        else name
                    )
                )
            )
            for name in fields
        ]
        parameters = tuple(
            (
                parent
                if name == "thread"
                else child if name == "agent" else "codex-agent-" + child
            )
            for name in fields
            if name in {"thread", "agent", "file"}
        )
        connection.execute(
            f"INSERT OR IGNORE INTO {table} SELECT {','.join(expressions)} FROM {table} WHERE thread=?",
            (*parameters, child),
        )
        connection.execute(f"DELETE FROM {table} WHERE thread=?", (child,))
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='session_events'"
    ).fetchone():
        events: object = connection.execute(
            "SELECT id,host,thread,agent,kind,at,until,amount_picos,ref,file,offset,device,inode FROM session_events WHERE thread=?",
            (child,),
        ).fetchall()
        for raw in sequence(events, "folded diagnostic events"):
            values = list(row(raw, 13))
            values[2], values[3] = parent, values[3] or child
            values[9] = values[9] or "codex-agent-" + child
            values[0] = hashlib.sha256(
                f"{parent}:{values[9]}:{values[10]}:{values[4]}:{values[8]}".encode()
            ).hexdigest()
            connection.execute(
                "INSERT OR IGNORE INTO session_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
        connection.execute("DELETE FROM session_events WHERE thread=?", (child,))
        connection.execute(
            "INSERT OR IGNORE INTO diagnostic_replays SELECT ?,CASE WHEN file='' THEN ? ELSE file END FROM diagnostic_replays WHERE task=?",
            (parent, "codex-agent-" + child, child),
        )
        connection.execute("DELETE FROM diagnostic_replays WHERE task=?", (child,))
    # Retained quotes use globally unique response ids and are untouched.
    connection.execute(
        "UPDATE responses SET task=?,agent=? WHERE task=? AND host='codex'",
        (parent, child, child),
    )
    connection.execute(
        "INSERT INTO turn_models SELECT ?,turn,model,observed,conflicted,? FROM turn_models WHERE task=? ON CONFLICT(task,turn,agent) DO UPDATE SET conflicted=turn_models.conflicted OR turn_models.model<>excluded.model",
        (parent, child, child),
    )
    connection.execute("DELETE FROM turn_models WHERE task=?", (child,))
    connection.execute(
        "INSERT OR IGNORE INTO sources SELECT ?,CASE WHEN file='' THEN ? ELSE file END,path,device,inode,position,skipping,scanned,remaining,incomplete,error,host,mtime_ns,size FROM sources WHERE task=? AND host='codex'",
        (parent, "codex-agent-" + child, child),
    )
    connection.execute("DELETE FROM sources WHERE task=? AND host='codex'", (child,))
    connection.execute(
        "INSERT OR IGNORE INTO gaps SELECT ?,CASE WHEN file='' THEN ? ELSE file END,device,inode,position,detail FROM gaps WHERE task=?",
        (parent, "codex-agent-" + child, child),
    )
    connection.execute("DELETE FROM gaps WHERE task=?", (child,))
    connection.execute(
        "INSERT OR IGNORE INTO collection_links SELECT ?,bead,relation FROM collection_links WHERE task=?",
        (parent, child),
    )
    connection.execute("DELETE FROM collection_links WHERE task=?", (child,))
    connection.execute(
        "INSERT OR IGNORE INTO collection_reasons SELECT ?,reason,ref FROM collection_reasons WHERE task=?",
        (parent, child),
    )
    connection.execute("DELETE FROM collection_reasons WHERE task=?", (child,))
    connection.execute("DELETE FROM collection_tasks WHERE task=?", (child,))


def normalize(connection: sqlite3.Connection) -> None:
    values: object = connection.execute(
        "SELECT thread,root FROM codex_roots WHERE thread<>root AND (thread IN (SELECT task FROM collection_tasks) OR thread IN (SELECT task FROM responses) OR thread IN (SELECT task FROM sources))"
    ).fetchall()
    for raw in sequence(values, "Codex roots"):
        child, parent = row(raw, 2)
        fold(connection, string(child, "child"), string(parent, "root"))
