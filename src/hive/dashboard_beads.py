"""Cache the native list fields once; detail reads stay native and read-only."""

import sqlite3
from datetime import UTC, datetime

from hive.bead_queries import BEAD
from hive.dashboard_values import packed
from hive.jsonvalue import integer, record, sequence, string
from hive.usage import timestamp
from hive.usage_store import row

FIELDS: frozenset[str] = frozenset(
    {
        "dependency_type",
        "type",
        "depends_on_id",
        "blocking_count",
        "id",
        "title",
        "status",
        "priority",
        "issue_type",
        "assignee",
        "owner",
        "created_at",
        "updated_at",
        "closed_at",
        "close_reason",
        "description",
        "acceptance_criteria",
        "notes",
        "metadata",
        "dependencies",
        "defer_until",
        "dependency_count",
        "dependent_count",
    }
)


def sanitized(raw: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in raw.items():
        if key not in FIELDS:
            continue
        if isinstance(value, str):
            result[key] = value[:4096]
        elif value is None or isinstance(value, (bool, int)):
            result[key] = value
        elif key == "metadata" and isinstance(value, dict):
            result[key] = {
                k: (v[:4096] if isinstance(v, str) else v)
                for k, v in record(value).items()
                if v is None or isinstance(v, (str, int, bool, float))
            }
        elif key == "dependencies" and isinstance(value, list):
            result[key] = [sanitized(record(v)) for v in value[:1000]]
    return result


def cache(connection: sqlite3.Connection, listed: object) -> None:
    now = datetime.now(UTC).isoformat()
    values = [sanitized(record(v, "bead")) for v in sequence(listed, "beads")]
    states = {string(v.get("id"), "bead"): v.get("status") for v in values}
    for value in values:
        identity = string(value.get("id"), "bead")
        if not BEAD.fullmatch(identity):
            continue
        metadata = record(value.get("metadata") or {})
        project = metadata.get("hive_project") or "Other"
        project = string(project, "project")
        dependencies = sequence(value.get("dependencies", []), "dependencies")
        blockers = sum(
            1
            for raw in dependencies
            if record(raw).get("dependency_type", record(raw).get("type", "blocks"))
            == "blocks"
            and states.get(
                str(record(raw).get("id", record(raw).get("depends_on_id"))),
                record(raw).get("status"),
            )
            != "closed"
        )
        # Native list versions may expose only the open blocker count.
        if not dependencies and isinstance(value.get("blocking_count"), int):
            blockers = integer(value["blocking_count"], "blockers")
        updated = timestamp(
            value.get("updated_at", value.get("created_at"))
        ).isoformat()
        created = timestamp(value.get("created_at")).isoformat()
        subtitle = next(
            (
                v.splitlines()[0]
                for key in ("close_reason", "notes", "description")
                if isinstance(v := value.get(key), str) and v.strip()
            ),
            "",
        )
        encoded = packed(value)
        previous: object = connection.execute(
            "SELECT payload,project FROM bead_rows WHERE bead=?", (identity,)
        ).fetchone()
        connection.execute(
            "INSERT INTO bead_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(bead) DO UPDATE SET project=excluded.project,status=excluded.status,title=excluded.title,updated=excluded.updated,created=excluded.created,closed=excluded.closed,assignee=excluded.assignee,blockers=excluded.blockers,resolution=excluded.resolution,deferred_until=excluded.deferred_until,subtitle=excluded.subtitle,payload=excluded.payload,refreshed=excluded.refreshed",
            (
                identity,
                project,
                string(value.get("status"), "status"),
                string(value.get("title"), "title", empty=True),
                updated,
                created,
                value.get("closed_at"),
                value.get("assignee"),
                blockers,
                metadata.get("hive_resolution"),
                value.get("defer_until"),
                subtitle[:4096],
                encoded,
                now,
            ),
        )
        if previous is None or row(previous, 2)[0] != encoded:
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_bead_dirty VALUES (?)", (identity,)
            )
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_dirty SELECT COALESCE(r.root,o.thread) FROM bead_seen_owners o LEFT JOIN codex_roots r ON o.thread=r.thread WHERE o.bead=?",
                (identity,),
            )

            if previous is None or row(previous, 2)[1] != project:
                connection.execute(
                    "INSERT INTO dashboard_versions SELECT DISTINCT COALESCE(r.root,o.thread),1 FROM bead_seen_owners o LEFT JOIN codex_roots r ON o.thread=r.thread WHERE o.bead=? ON CONFLICT(thread) DO UPDATE SET version=version+1",
                    (identity,),
                )
