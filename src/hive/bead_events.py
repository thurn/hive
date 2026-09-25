"""Validate Beads event shapes and use UUIDv7 milliseconds as UTC time."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from hive.bead_queries import BEAD, UUID7
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, record, sequence, string
from hive.usage import timestamp

FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "issue_id",
        "event_type",
        "old_status",
        "old_assignee",
        "new_status",
        "new_assignee",
        "created_at",
        "server_offset",
    }
)


def event_time(identity: str) -> datetime:
    if UUID7.fullmatch(identity) is None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Beads event ID is not a UUIDv7")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=UUID(identity).int >> 80
        )
    except OverflowError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Beads event timestamp out of range"
        ) from error


def text(value: object, name: str) -> str | None:
    if value is None:
        return None
    result = string(value, name, empty=True)
    return "" if result == "null" else result


@dataclass(frozen=True)
class Event:
    identity: str
    bead: str
    kind: str
    old_status: str
    old_assignee: str
    new_status: str | None
    new_assignee: str | None
    occurred: datetime | None
    error: str | None

    @classmethod
    def read(cls, value: object) -> "Event":
        raw = record(value, "Beads event")
        if raw.keys() != FIELDS:
            raise HiveError(ErrorCode.INVALID_RECORD, "Unexpected Beads events schema")
        identity = string(raw["id"], "Beads event ID")
        bead = string(raw["issue_id"], "event bead")
        if BEAD.fullmatch(bead) is None:
            raise HiveError(ErrorCode.INVALID_RECORD, "Invalid event bead ID")
        kind = string(raw["event_type"], "event type")
        occurred: datetime | None = None
        error: str | None = None
        try:
            if kind in {"label_added", "label_removed"} and any(
                raw[name] is not None
                for name in ("old_status", "old_assignee", "new_status", "new_assignee")
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    "Label event unexpectedly changes ownership",
                )
            occurred = event_time(identity)
            local = timestamp(raw["created_at"])
            offset = integer(raw["server_offset"], "server offset", minimum=-86400)
            if (
                offset > 86400
                or min(
                    abs(
                        (
                            local - timedelta(seconds=offset + dst) - occurred
                        ).total_seconds()
                    )
                    for dst in (-3600, 0, 3600)
                )
                > 2
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Beads event time disagrees with UUIDv7"
                )
        except HiveError as failure:
            error = failure.detail
        return cls(
            identity,
            bead,
            kind,
            text(raw["old_status"], "old status") or "",
            text(raw["old_assignee"], "old assignee") or "",
            text(raw["new_status"], "new status"),
            text(raw["new_assignee"], "new assignee"),
            occurred,
            error,
        )


def read(value: object) -> tuple[Event, ...]:
    return tuple(Event.read(raw) for raw in sequence(value, "Beads event page"))
