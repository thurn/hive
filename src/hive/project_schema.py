"""Project discovery and canonical root identity are disposable derived facts."""

import sqlite3


def create(connection: sqlite3.Connection) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='project_sessions' AND type='table'"
        ).fetchone()
        is not None
    ):
        return
    for statement in (
        "CREATE TABLE cwd_projects (cwd TEXT PRIMARY KEY, configuration TEXT NOT NULL, project TEXT, unresolved INTEGER NOT NULL)",
        "CREATE TABLE codex_roots (thread TEXT PRIMARY KEY, root TEXT NOT NULL)",
        "CREATE INDEX codex_roots_root ON codex_roots(root)",
        "CREATE TABLE project_sessions (thread TEXT PRIMARY KEY, host TEXT NOT NULL, project TEXT, cwd TEXT NOT NULL, path TEXT NOT NULL, created TEXT NOT NULL, updated INTEGER NOT NULL, parent TEXT, title TEXT, agent_role TEXT)",
        "CREATE TABLE discovery_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE claude_discovery_files (path TEXT PRIMARY KEY, device INTEGER NOT NULL, inode INTEGER NOT NULL, size INTEGER NOT NULL, position INTEGER NOT NULL, matched INTEGER NOT NULL)",
        "CREATE TABLE collection_reasons (task TEXT NOT NULL, reason TEXT NOT NULL, ref TEXT NOT NULL, PRIMARY KEY(task,reason,ref))",
        "ALTER TABLE sources ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sources ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
    ):
        connection.execute(statement)
    connection.execute(
        "INSERT OR IGNORE INTO collection_reasons SELECT task,'link',task FROM collection_tasks"
    )
    # Child and parent can use the same native turn id; model evidence remains
    # scoped to the producing agent instead of conflating their contexts.
    connection.execute("DROP VIEW IF EXISTS request_detail")
    connection.execute("ALTER TABLE turn_models RENAME TO old_turn_models")
    connection.execute(
        "CREATE TABLE turn_models (task TEXT NOT NULL, turn TEXT NOT NULL, model TEXT NOT NULL, observed TEXT NOT NULL, conflicted INTEGER NOT NULL, agent TEXT NOT NULL DEFAULT '', PRIMARY KEY(task,turn,agent))"
    )
    connection.execute(
        "INSERT INTO turn_models SELECT task,turn,model,observed,conflicted,'' FROM old_turn_models"
    )
    connection.execute("DROP TABLE old_turn_models")
    from hive.request_detail_schema import VIEW

    connection.execute("DROP VIEW IF EXISTS request_detail")
    connection.execute(VIEW)
