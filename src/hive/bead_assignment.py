"""Ownership is exact except equal sharing, with integer-picodollar remainders."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from hive.bead_requests import Request
from hive.identity import ThreadId
from hive.jsonvalue import integer, sequence, string
from hive.usage import timestamp
from hive.usage_store import row


@dataclass(frozen=True)
class Ownership:
    bead: str
    ordinal: int
    thread: ThreadId
    start: datetime
    end: datetime | None
    deleted: bool

    def contains(self, at: datetime) -> bool:
        return self.start <= at and (self.end is None or at < self.end)


@dataclass(frozen=True)
class Unknown:
    bead: str
    thread: ThreadId
    start: datetime | None


@dataclass(frozen=True)
class Assignment:
    request: Request
    shares: tuple[tuple[Ownership, int], ...]
    kind: str
    near_boundary: bool


@dataclass(frozen=True)
class Evidence:
    intervals: tuple[Ownership, ...]
    unknown: tuple[Unknown, ...]
    caught_up: bool

    @classmethod
    def read(cls, connection: sqlite3.Connection) -> "Evidence":
        fetched: object = connection.execute(
            "SELECT i.bead,i.ordinal,i.thread,i.start,i.end,r.deleted FROM bead_intervals i "
            "JOIN bead_replays r ON i.bead=r.bead WHERE r.status='known' AND r.renamed_to IS NULL ORDER BY i.bead,i.ordinal"
        ).fetchall()
        intervals: list[Ownership] = []
        for value in sequence(fetched, "ownership intervals"):
            bead, ordinal, thread, start, end, deleted = row(value, 6)
            intervals.append(
                Ownership(
                    string(bead, "bead"),
                    integer(ordinal, "ordinal"),
                    ThreadId(string(thread, "owner")),
                    timestamp(start),
                    None if end is None else timestamp(end),
                    bool(deleted),
                )
            )
        fetched = connection.execute(
            "SELECT o.bead,o.thread,(SELECT MIN(e.occurred) FROM bead_events e WHERE e.bead=o.bead AND e.kind='created' AND e.error IS NULL) "
            "FROM bead_seen_owners o JOIN bead_snapshots s ON o.bead=s.bead "
            "LEFT JOIN bead_replays r ON o.bead=r.bead WHERE r.status IS NULL OR r.status IN ('unknown','pending')"
        ).fetchall()
        unknown: list[Unknown] = []
        for value in sequence(fetched, "unknown intervals"):
            bead, thread, start = row(value, 3)
            unknown.append(
                Unknown(
                    string(bead, "bead"),
                    ThreadId(string(thread, "owner")),
                    None if start is None else timestamp(start),
                )
            )
        health: object = connection.execute(
            "SELECT caught_up,error FROM bead_event_cursor WHERE singleton=1"
        ).fetchone()
        caught, error = (0, None) if health is None else row(health, 2)
        return cls(tuple(intervals), tuple(unknown), bool(caught) and error is None)

    def assign(self, request: Request) -> Assignment:
        if request.amount is None:
            return Assignment(request, (), "unpriced", False)
        owner = request.thread
        if (
            request.host == "codex"
            and request.agent is not None
            and (
                any(i.thread == request.agent for i in self.intervals)
                or any(u.thread == request.agent for u in self.unknown)
            )
        ):
            owner = ThreadId(request.agent)
        if not self.caught_up or any(
            unknown.thread == owner
            and (unknown.start is None or request.at >= unknown.start)
            for unknown in self.unknown
        ):
            return Assignment(request, (), "unattributable", False)
        relevant = [interval for interval in self.intervals if interval.thread == owner]
        near = any(
            abs((request.at - boundary).total_seconds()) <= 2
            for interval in relevant
            for boundary in (interval.start, interval.end)
            if boundary is not None
        )
        active = [interval for interval in relevant if interval.contains(request.at)]
        if not active:
            return Assignment(request, (), "unowned", near)
        quotient, remainder = divmod(request.amount, len(active))
        return Assignment(
            request,
            tuple(
                (interval, quotient + int(index < remainder))
                for index, interval in enumerate(active)
            ),
            "shared" if len(active) > 1 else "owned",
            near,
        )


def hour(value: datetime) -> str:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()
