"""Bounded metadata and source offsets, never transcript or command text."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='tool_calls'"
        ).fetchone()
        is not None
    ):
        return
    for statement in (
        """CREATE TABLE tool_calls (
          host TEXT NOT NULL,thread TEXT NOT NULL,agent TEXT NOT NULL,call_id TEXT NOT NULL,
          tool TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,duration_ms INTEGER,
          status TEXT NOT NULL,input_hash8 TEXT NOT NULL,input_bytes INTEGER NOT NULL,
          result_bytes INTEGER NOT NULL DEFAULT 0,file TEXT NOT NULL,use_offset INTEGER NOT NULL,
          result_offset INTEGER,use_device INTEGER NOT NULL,use_inode INTEGER NOT NULL,
          result_device INTEGER,result_inode INTEGER,command_kind TEXT NOT NULL,
          PRIMARY KEY(thread,agent,call_id))""",
        "CREATE INDEX tool_calls_thread ON tool_calls(thread,started_at)",
        """CREATE TABLE tool_commands (
          thread TEXT NOT NULL,agent TEXT NOT NULL,call_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
          exit_code INTEGER,wall_ms INTEGER,command_hash8 TEXT NOT NULL,command_kind TEXT NOT NULL,
          PRIMARY KEY(thread,agent,call_id,ordinal))""",
        """CREATE TABLE session_events (
          id TEXT PRIMARY KEY,host TEXT NOT NULL,thread TEXT NOT NULL,agent TEXT NOT NULL,
          kind TEXT NOT NULL,at TEXT NOT NULL,until TEXT,amount_picos TEXT,ref TEXT,
          file TEXT NOT NULL,offset INTEGER NOT NULL,device INTEGER NOT NULL,inode INTEGER NOT NULL)""",
        "CREATE INDEX session_events_thread ON session_events(thread,at)",
        "CREATE TABLE role_spans (thread TEXT NOT NULL,agent TEXT NOT NULL,role TEXT NOT NULL,start TEXT NOT NULL,inherited INTEGER NOT NULL,evidence TEXT NOT NULL,PRIMARY KEY(thread,agent,start,role))",
        "CREATE TABLE diagnostic_titles (thread TEXT NOT NULL,agent TEXT NOT NULL,role TEXT,offset INTEGER NOT NULL,PRIMARY KEY(thread,agent))",
        "CREATE TABLE diagnostic_sessions (thread TEXT NOT NULL,agent TEXT NOT NULL,last_record TEXT NOT NULL,spawned TEXT NOT NULL,PRIMARY KEY(thread,agent))",
        "CREATE TABLE diagnostic_replays (task TEXT NOT NULL,file TEXT NOT NULL,PRIMARY KEY(task,file))",
        "INSERT INTO diagnostic_replays SELECT task,file FROM sources",
    ):
        connection.execute(statement)
