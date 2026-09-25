"""Versioned event observations, ingestion cursors and privacy-safe gaps."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    for statement in (
        """CREATE TABLE IF NOT EXISTS claude_event_sequences (
            task TEXT NOT NULL, occurred TEXT NOT NULL, sequence INTEGER NOT NULL,
            kind TEXT NOT NULL, version TEXT, PRIMARY KEY(task,occurred,sequence))""",
        """CREATE TABLE IF NOT EXISTS claude_request_events (
            response TEXT PRIMARY KEY, request_id TEXT, client_id TEXT,
            task TEXT NOT NULL, occurred TEXT NOT NULL, sequence INTEGER NOT NULL,
            observed TEXT NOT NULL, prompt TEXT, model TEXT NOT NULL,
            input INTEGER NOT NULL, cached INTEGER NOT NULL, writes INTEGER NOT NULL,
            output INTEGER NOT NULL, micros INTEGER NOT NULL, host_usd TEXT NOT NULL, duration INTEGER NOT NULL,
            speed TEXT, query_source TEXT, agent_name TEXT, skill TEXT, plugin TEXT,
            mcp_server TEXT, mcp_tool TEXT, effort TEXT, version TEXT,
            usage TEXT NOT NULL, modifiers TEXT NOT NULL, modifier_key TEXT NOT NULL,
            flags TEXT NOT NULL, attributes TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS claude_events_task ON claude_request_events(task)",
        """CREATE TABLE IF NOT EXISTS claude_request_errors (
            task TEXT NOT NULL, occurred TEXT NOT NULL, sequence INTEGER NOT NULL,
            PRIMARY KEY(task,occurred,sequence))""",
        """CREATE TABLE IF NOT EXISTS claude_event_gaps (
            file TEXT NOT NULL, position INTEGER NOT NULL, task TEXT,
            observed TEXT NOT NULL, detail TEXT NOT NULL,
            PRIMARY KEY(file,position,detail))""",
        """CREATE TABLE IF NOT EXISTS otlp_ingest_files (
            file TEXT PRIMARY KEY, position INTEGER NOT NULL,
            device INTEGER NOT NULL, inode INTEGER NOT NULL, size INTEGER NOT NULL)""",
    ):
        connection.execute(statement)
    connection.execute(
        "ALTER TABLE claude_cost_states ADD COLUMN timestamp_known INTEGER NOT NULL DEFAULT 1"
    )
    # Claude 2.1.282 omits a wall-clock timestamp; duration only orders totals.
    # Replay main files to recover totals previously rejected for missing ISO
    # timestamps. Real malformed totals are rediscovered by the bounded replay.
    connection.execute(
        "DELETE FROM gaps WHERE file='' AND detail LIKE 'Invalid cost-state:%'"
    )
    connection.execute(
        "UPDATE sources SET position=0,skipping=0,remaining=NULL WHERE host='claude' AND file=''"
    )
