"""Ownership evidence and replay health remain disposable local observations."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE bead_snapshots ADD COLUMN created TEXT")
    connection.execute(
        "ALTER TABLE bead_snapshots ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1"
    )
    connection.execute(
        "ALTER TABLE bead_snapshots ADD COLUMN native_missing INTEGER NOT NULL DEFAULT 0"
    )
    connection.execute(
        """CREATE TABLE bead_replays (
        bead TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
        status TEXT NOT NULL, reason TEXT, stable INTEGER NOT NULL,
        rebuild INTEGER NOT NULL, deleted INTEGER NOT NULL, renamed_to TEXT, last_event TEXT NOT NULL, checked TEXT NOT NULL)"""
    )
    connection.execute("""CREATE TABLE bead_intervals (
        bead TEXT NOT NULL, ordinal INTEGER NOT NULL, thread TEXT NOT NULL,
        start TEXT NOT NULL, end TEXT, PRIMARY KEY(bead,ordinal))""")
    connection.execute(
        "CREATE INDEX bead_intervals_thread ON bead_intervals(thread,start)"
    )
    connection.execute("""CREATE TABLE bead_seen_owners (
        bead TEXT NOT NULL, thread TEXT NOT NULL, PRIMARY KEY(bead,thread))""")
    # New interval evidence must be replayed before retention can use catch-up.
    connection.execute("UPDATE bead_event_cursor SET caught_up=0")
