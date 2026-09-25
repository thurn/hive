"""Split each bead's request share across the same estimated tool buckets."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass

from hive.bead_assignment import Assignment
from hive.identity import ThreadId
from hive.jsonvalue import sequence, string
from hive.pricing import dollars, picos
from hive.tool_allocation import split
from hive.usage_store import row


@dataclass(frozen=True)
class ToolShare:
    bucket: str
    tool: str | None
    amount: int


def allocations(
    connection: sqlite3.Connection, threads: tuple[ThreadId, ...]
) -> dict[str, tuple[ToolShare, ...]]:
    """Read materialized shares in the same snapshot as their retained request totals."""
    fetched: object = connection.execute(
        "SELECT a.response,a.bucket,a.tool_name,a.usd "
        "FROM tool_allocation a JOIN responses r ON r.response=a.response "
        "WHERE r.task IN (SELECT value FROM json_each(?)) ORDER BY a.response,a.ordinal",
        (json.dumps(threads),),
    ).fetchall()
    result: dict[str, list[ToolShare]] = {}
    for raw in sequence(fetched, "tool allocations"):
        response, bucket, tool, usd = row(raw, 4)
        result.setdefault(string(response, "response"), []).append(
            ToolShare(
                string(bucket, "bucket"),
                None if tool is None else string(tool, "tool"),
                picos(usd),
            )
        )
    return {response: tuple(charges) for response, charges in result.items()}


def breakdown(
    assignments: tuple[Assignment, ...],
    bead: str,
    allocated: dict[str, tuple[ToolShare, ...]],
) -> list[dict[str, object]]:
    totals: Counter[tuple[str, str | None]] = Counter()
    for assignment in assignments:
        for interval, amount in assignment.shares:
            if interval.bead != bead:
                continue
            request = assignment.request
            charges = allocated.get(request.response)
            if not charges:
                totals[
                    (
                        (
                            "event_only"
                            if request.source == "events"
                            else (
                                "codex"
                                if request.host == "codex"
                                else "allocation_unavailable"
                            )
                        ),
                        None,
                    )
                ] += amount
                continue
            for charge, share in zip(
                charges,
                split(amount, tuple(charge.amount for charge in charges)),
                strict=True,
            ):
                totals[(charge.bucket, charge.tool)] += share
    result: list[dict[str, object]] = []
    for (bucket, tool), amount in sorted(totals.items()):
        result.append({"bucket": bucket, "tool": tool, "usd": dollars(amount)})
    return result
