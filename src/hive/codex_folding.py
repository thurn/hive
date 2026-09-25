"""One root owns each Codex response; native child identity stays observable."""

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
