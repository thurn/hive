"""Observe spawning tool IDs without copying prompt or tool content."""

import sqlite3

from hive.identity import ThreadId
from hive.jsonvalue import record, sequence, string


def create(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE claude_tool_owners (
        task TEXT NOT NULL, tool TEXT NOT NULL, agent TEXT NOT NULL,
        PRIMARY KEY(task,tool,agent))""")
    connection.execute("""CREATE VIEW claude_agent_parents AS
        SELECT a.*, CASE WHEN COUNT(t.tool)=1 THEN NULLIF(MIN(t.agent),'')
        ELSE 'unknown' END AS parent_agent
        FROM claude_agents a LEFT JOIN claude_tool_owners t
        ON a.task=t.task AND a.tool_use_id=t.tool
        GROUP BY a.task,a.agent""")
    # Rebuild the small identity index through ordinary bounded file replay.
    connection.execute(
        "UPDATE sources SET position=0,skipping=0,remaining=NULL WHERE host='claude'"
    )


def observe(
    connection: sqlite3.Connection, task: ThreadId, raw: dict[str, object]
) -> None:
    if raw.get("type") != "assistant":
        return
    message = record(raw.get("message"), "Claude message")
    content = message.get("content", [])
    if isinstance(content, str):
        return
    agent = "" if raw.get("agentId") is None else string(raw["agentId"], "agent ID")
    for item in sequence(content, "assistant content"):
        block = record(item, "assistant content block")
        if block.get("type") != "tool_use":
            continue
        tool = string(block.get("id"), "tool use ID")
        connection.execute(
            "INSERT OR IGNORE INTO claude_tool_owners(task,tool,agent) VALUES (?,?,?)",
            (task, tool, agent),
        )
