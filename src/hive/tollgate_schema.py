"""Small disposable Tollgate observations; logs stay with Tollgate."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='tollgate_candidates'"
        ).fetchone()
        is not None
    ):
        return
    for statement in (
        "CREATE TABLE tollgate_promotions(candidate TEXT PRIMARY KEY,at TEXT NOT NULL)",
        "CREATE TABLE tollgate_mapping_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),signature TEXT NOT NULL,refreshed TEXT NOT NULL)",
        "CREATE TABLE tollgate_repositories(project TEXT PRIMARY KEY,repository TEXT NOT NULL,path TEXT NOT NULL,refreshed TEXT NOT NULL)",
        "CREATE TABLE tollgate_mentions(thread TEXT NOT NULL,agent TEXT NOT NULL,candidate TEXT NOT NULL,first_seen TEXT NOT NULL,PRIMARY KEY(thread,agent,candidate))",
        "CREATE TABLE tollgate_pending(candidate TEXT NOT NULL,repository TEXT NOT NULL,checked TEXT,error TEXT,PRIMARY KEY(candidate,repository))",
        "CREATE TABLE tollgate_candidates(candidate TEXT PRIMARY KEY,repository TEXT NOT NULL,project TEXT NOT NULL,state TEXT NOT NULL,terminal INTEGER NOT NULL,updated TEXT NOT NULL,observed TEXT NOT NULL,payload TEXT NOT NULL)",
        "CREATE TABLE tollgate_branch_matches(candidate TEXT NOT NULL,bead TEXT NOT NULL,PRIMARY KEY(candidate,bead))",
        "CREATE TABLE tollgate_health(singleton INTEGER PRIMARY KEY CHECK(singleton=1),refreshed TEXT,error TEXT,behind INTEGER NOT NULL)",
        "INSERT INTO tollgate_health VALUES (1,NULL,NULL,1)",
        "INSERT OR IGNORE INTO diagnostic_replays SELECT task,file FROM sources",
    ):
        connection.execute(statement)
