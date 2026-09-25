"""Atomic, versioned upgrades of the disposable observation database."""

import json
import sqlite3

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, parse, sequence, string
from hive.usage import tokens

VERSION = 11

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sources (
        task TEXT NOT NULL, file TEXT NOT NULL DEFAULT '', path TEXT NOT NULL,
        device INTEGER NOT NULL, inode INTEGER NOT NULL,
        position INTEGER NOT NULL, skipping INTEGER NOT NULL,
        scanned TEXT NOT NULL, remaining INTEGER, incomplete INTEGER, error TEXT,
        PRIMARY KEY(task, file))""",
    """CREATE TABLE IF NOT EXISTS responses (
        response TEXT PRIMARY KEY, task TEXT NOT NULL, turn TEXT, observed TEXT NOT NULL,
        usage TEXT, input INTEGER, cached INTEGER, cache_write INTEGER,
        output INTEGER, reasoning INTEGER,
        host TEXT NOT NULL DEFAULT 'codex', agent TEXT, model TEXT,
        modifier_key TEXT, modifiers TEXT, cache_write_1h INTEGER NOT NULL DEFAULT 0,
        complete INTEGER NOT NULL DEFAULT 1, skill TEXT)""",
    "CREATE INDEX IF NOT EXISTS responses_task ON responses(task)",
    """CREATE TABLE IF NOT EXISTS turn_models (
        task TEXT NOT NULL, turn TEXT NOT NULL, model TEXT NOT NULL,
        observed TEXT NOT NULL, conflicted INTEGER NOT NULL, PRIMARY KEY(task, turn))""",
    """CREATE TABLE IF NOT EXISTS response_estimates (
        response TEXT NOT NULL, tier TEXT NOT NULL, quote TEXT NOT NULL,
        PRIMARY KEY(response, tier))""",
    """CREATE TABLE IF NOT EXISTS gaps (
        task TEXT NOT NULL, file TEXT NOT NULL DEFAULT '', device INTEGER NOT NULL,
        inode INTEGER NOT NULL, position INTEGER NOT NULL, detail TEXT NOT NULL,
        PRIMARY KEY(task, file, device, inode, position, detail))""",
    """CREATE TABLE IF NOT EXISTS collection_tasks (
        task TEXT PRIMARY KEY, attempted TEXT, error TEXT, validated_path TEXT,
        host TEXT)""",
    """CREATE TABLE IF NOT EXISTS collection_links (
        task TEXT NOT NULL, bead TEXT NOT NULL, relation TEXT NOT NULL,
        PRIMARY KEY(task,bead,relation))""",
    "CREATE TABLE IF NOT EXISTS collection_gaps (detail TEXT PRIMARY KEY)",
    """CREATE TABLE IF NOT EXISTS collection_health (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), refreshed TEXT, error TEXT)""",
)


def add_claude(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE claude_response_counts (
        response TEXT NOT NULL, output INTEGER NOT NULL, reasoning INTEGER NOT NULL,
        PRIMARY KEY(response,output,reasoning))""")
    connection.execute(
        "ALTER TABLE responses ADD COLUMN flags TEXT NOT NULL DEFAULT '[]'"
    )
    connection.execute(
        "ALTER TABLE sources ADD COLUMN host TEXT NOT NULL DEFAULT 'codex'"
    )
    connection.execute("""CREATE TABLE claude_agents (
        task TEXT NOT NULL, agent TEXT NOT NULL, tool_use_id TEXT, agent_type TEXT,
        description TEXT, spawn_depth INTEGER, PRIMARY KEY(task,agent))""")


def add_cost_states(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE responses ADD COLUMN last_observed TEXT")
    connection.execute("UPDATE responses SET last_observed=observed")
    # Replay Claude files in the normal per-file byte budget to recover host
    # totals and the last streamed usage time; first request times stay fixed.
    connection.execute(
        "UPDATE sources SET position=0,skipping=0,remaining=NULL WHERE host='claude'"
    )
    connection.execute("""CREATE TABLE claude_cost_states (
        task TEXT NOT NULL, start TEXT NOT NULL, observed TEXT NOT NULL,
        usd TEXT NOT NULL, unknown_model INTEGER NOT NULL,
        PRIMARY KEY(task,start))""")


def version(connection: sqlite3.Connection) -> int:
    raw: object = connection.execute("PRAGMA user_version").fetchone()
    if not isinstance(raw, tuple) or len(raw) != 1:
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid telemetry schema version")
    return integer(raw[0], "telemetry schema version")


def tables(connection: sqlite3.Connection) -> set[str]:
    fetched: object = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names: set[str] = set()
    for raw in sequence(fetched, "telemetry tables"):
        if not isinstance(raw, tuple) or len(raw) != 1:
            raise HiveError(ErrorCode.INVALID_RECORD, "Invalid telemetry table")
        names.add(string(raw[0], "telemetry table"))
    return names


def prepare(connection: sqlite3.Connection, *, write: bool) -> None:
    current = version(connection)
    if current > VERSION:
        raise HiveError(ErrorCode.INVALID_RECORD, "Telemetry store has a newer schema")
    if current == VERSION:
        return
    existing = tables(connection)
    if not write and existing:
        raise HiveError(
            ErrorCode.INVALID_RECORD,
            "Telemetry store not yet migrated; run a collector write",
        )
    # Only new stores may be initialized by a reader; an existing store is
    # upgraded by the collector. Recheck under the lock for concurrent openers.
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            current = version(connection)
            if current > VERSION:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Telemetry store has a newer schema"
                )
            if current == VERSION:
                return
            if current in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
                if current == 1:
                    add_claude(connection)
                if current < 3:
                    add_cost_states(connection)
                from hive.event_schema import create as create_events
                from hive.request_detail_schema import create

                if current < 5:
                    create_events(connection)
                from hive.agent_store import create as create_agents

                if current < 6:
                    create_agents(connection)
                from hive.bead_schema import create as create_beads

                if current < 7:
                    create_beads(connection)
                from hive.bead_interval_schema import create as create_intervals

                if current < 8:
                    create_intervals(connection)
                from hive.tool_schema import create as create_tools

                if current < 9:
                    create_tools(connection)
                connection.execute("UPDATE claude_request_events SET observed=occurred")
                create(connection)
                from hive.tool_allocation_store import create as create_allocations

                create_allocations(connection)
                connection.execute(f"PRAGMA user_version={VERSION}")
                return
            existing = tables(connection)
            for name in ("sources", "responses", "gaps"):
                if name in existing:
                    connection.execute(f"ALTER TABLE {name} RENAME TO legacy_{name}")
            # The old index follows the renamed table; recreate it below.
            connection.execute("DROP INDEX IF EXISTS responses_task")
            if "collection_tasks" in existing:
                connection.execute("ALTER TABLE collection_tasks ADD COLUMN host TEXT")
                connection.execute("UPDATE collection_tasks SET host='codex'")
            for statement in SCHEMA:
                connection.execute(statement)
            if "sources" in existing:
                connection.execute(
                    "INSERT INTO sources(task,path,device,inode,position,skipping,scanned,remaining,incomplete,error) "
                    "SELECT task,path,device,inode,position,skipping,scanned,remaining,incomplete,error FROM legacy_sources"
                )
            if "gaps" in existing:
                connection.execute(
                    "INSERT INTO gaps(task,device,inode,position,detail) "
                    "SELECT task,device,inode,position,detail FROM legacy_gaps"
                )
            if "responses" in existing:
                connection.execute(
                    "INSERT INTO responses(response,task,turn,observed,usage,input,cached,cache_write,output,reasoning) "
                    "SELECT response,task,turn,observed,usage,input,cached,cache_write,output,reasoning FROM legacy_responses"
                )
                cursor = connection.execute(
                    "SELECT response, usage FROM responses WHERE usage IS NOT NULL"
                )
                while True:
                    fetched: object = cursor.fetchmany(256)
                    batch = sequence(fetched, "legacy usage batch")
                    if not batch:
                        break
                    for raw in batch:
                        if not isinstance(raw, tuple) or len(raw) != 2:
                            raise HiveError(
                                ErrorCode.INVALID_RECORD, "Invalid legacy usage"
                            )
                        response = string(raw[0], "legacy response")
                        usage = tokens(parse(string(raw[1], "legacy usage")))
                        connection.execute(
                            "UPDATE responses SET usage=? WHERE response=?",
                            (json.dumps(usage.value(), sort_keys=True), response),
                        )
            for name in ("sources", "responses", "gaps"):
                if name in existing:
                    connection.execute(f"DROP TABLE legacy_{name}")
            add_claude(connection)
            add_cost_states(connection)
            from hive.event_schema import create as create_events
            from hive.request_detail_schema import create

            create_events(connection)
            from hive.agent_store import create as create_agents

            create_agents(connection)
            from hive.bead_schema import create as create_beads

            create_beads(connection)
            from hive.bead_interval_schema import create as create_intervals

            create_intervals(connection)
            from hive.tool_schema import create as create_tools

            create_tools(connection)
            create(connection)
            from hive.tool_allocation_store import create as create_allocations

            create_allocations(connection)
            connection.execute(f"PRAGMA user_version={VERSION}")
    finally:
        connection.execute("PRAGMA busy_timeout=100")
