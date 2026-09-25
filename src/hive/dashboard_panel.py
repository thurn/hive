"""Native read-only Beads detail with a bounded cached fallback."""

import shlex
import sqlite3
import time

from hive.bead_queries import BEAD
from hive.beads_connection import BeadsConnection
from hive.dashboard_beads import sanitized
from hive.dashboard_process import run
from hive.dashboard_values import rows
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, sequence, string
from hive.launch_context import LaunchContext


def history(identifier: str) -> str:
    if BEAD.fullmatch(identifier) is None:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid bead ID")
    return f"SELECT id,event_type,actor,LEFT(old_value,1024) AS old_value,LEFT(new_value,1024) AS new_value,created_at FROM events WHERE issue_id='{identifier}' ORDER BY id"


def panel(
    connection: sqlite3.Connection, context: LaunchContext, bead: str
) -> dict[str, object]:
    query = history(bead)
    cached = rows(
        connection, "SELECT payload,refreshed FROM bead_rows WHERE bead=?", (bead,)
    )
    result: dict[str, object] = dict(
        source="cache",
        bead=parse(string(cached[0]["payload"], "cached bead")) if cached else None,
        refreshed=cached[0]["refreshed"] if cached else None,
    )
    prefix = (
        "env BEADS_DIR="
        + shlex.quote(str(context.beads / ".beads"))
        + " BEADS_DOLT_AUTO_START=0 BD_NON_INTERACTIVE=1 BD_NO_HOOKS=true bd --sandbox --dolt-auto-commit off --actor '<actor>' "
    )
    result["commands"] = {
        name: prefix + " ".join(shlex.quote(a) for a in args)
        for name, args in (
            ("show", ("show", bead, "--json")),
            ("comments", ("comments", bead, "--json")),
            ("dependents", ("dep", "list", bead, "--direction=up", "--json")),
        )
    }
    deadline = time.monotonic() + 5
    try:
        native = BeadsConnection.read(context.beads)
        collected: dict[str, object] = {}
        for name, args in (
            ("bead", ("show", bead)),
            ("comments", ("comments", bead)),
            ("dependents", ("dep", "list", bead, "--direction=up")),
            ("events", ("sql", query)),
        ):
            output = run(
                ("bd", "--sandbox", "--dolt-auto-commit", "off", "--json", *args),
                native.directory,
                deadline - time.monotonic(),
                environment=native.environment(),
            )
            if output.truncated:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Beads detail exceeded size bound"
                )
            value = parse(output.content.decode())
            if name == "bead":
                value = (
                    sanitized(record(sequence(value, "bead details")[0]))
                    if isinstance(value, list)
                    else sanitized(record(value))
                )
            collected[name] = value
        result.update(collected)
        result["source"] = "live"
    except (HiveError, OSError, UnicodeError) as error:
        result["error"] = str(error)
    return result
