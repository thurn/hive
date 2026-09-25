"""Best-effort, bounded executor receipts; never task membership or authority."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hive.errors import HiveError
from hive.jsonvalue import parse, record, sequence
from hive.launch_context import LaunchContext
from hive.locking import file_lock
from hive.thread_links import thread_id

LIMIT = 128
MAX_BYTES = 262_144


@dataclass(frozen=True)
class Receipt:
    event: str
    outcome: str
    session: str | None
    turn: str | None = None
    stop_kind: str | None = None


def identity(value: object) -> str | None:
    """Only native UUIDs are retained; malformed payload text is never logged."""
    return thread_id(value)


def _path(context: LaunchContext) -> Path:
    return context.state / "executor-diagnostics.json"


def read(context: LaunchContext, session: str | None) -> dict[str, object]:
    """Return recent receipts; absence cannot prove the host never invoked us."""
    path = _path(context)
    try:
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
    except FileNotFoundError:
        data = b"[]"
    if len(data) > MAX_BYTES:
        raise ValueError("Executor diagnostics exceed storage limit")
    rows = tuple(record(item) for item in sequence(parse(data.decode()), "receipts"))
    return {
        "code": "ExecutorDiagnostics",
        "limit": LIMIT,
        "receipts": [
            row
            for row in rows[-LIMIT:]
            if session is None or row.get("session") == session
        ],
    }


def append(context: LaunchContext, receipt: Receipt) -> str | None:
    """One global ring bounds both session count and retained bytes.

    A separate short lock never spans Beads reads or activation writes. Failure
    cannot change the stop decision; callers surface a warning instead. Codes,
    UUIDs and selected commit are retained, not prompts or free-form reasons.
    """
    try:
        with file_lock(context.state / "locks/executor-diagnostics.lock", timeout=0.05):
            existing = read(context, None)
            rows = sequence(existing["receipts"], "receipts")
            entry = {
                **asdict(receipt),
                "timestamp": datetime.now(UTC).isoformat(),
                "source_commit": str(context.commit),
            }
            encoded = json.dumps([*rows[-(LIMIT - 1) :], entry]).encode()
            if len(encoded) > MAX_BYTES:
                raise ValueError("Executor diagnostics exceed storage limit")
            path = _path(context)
            temporary = path.with_suffix(f".{uuid4()}.tmp")
            try:
                with open(temporary, "xb", opener=_private) as output:
                    output.write(encoded)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return None
    except (HiveError, OSError, ValueError):
        return "Hive executor diagnostics unavailable; lifecycle evidence was not retained."


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)
