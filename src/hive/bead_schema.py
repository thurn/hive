"""Disposable event cache preserves historical owners across link refreshes."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE bead_events (
        id TEXT PRIMARY KEY, bead TEXT NOT NULL, kind TEXT NOT NULL,
        old_status TEXT NOT NULL, old_assignee TEXT NOT NULL,
        new_status TEXT, new_assignee TEXT, occurred TEXT, error TEXT)""")
    connection.execute("CREATE INDEX bead_events_bead ON bead_events(bead,id)")
    connection.execute("""CREATE TABLE bead_event_cursor (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), after_id TEXT NOT NULL,
        newest TEXT NOT NULL, caught_up INTEGER NOT NULL, error TEXT)""")
    connection.execute("""CREATE TABLE bead_snapshots (
        bead TEXT PRIMARY KEY, status TEXT NOT NULL, assignee TEXT NOT NULL,
        listed INTEGER NOT NULL, rebuilt INTEGER NOT NULL DEFAULT 0,
        unknown_reason TEXT)""")
