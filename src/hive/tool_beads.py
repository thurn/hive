"""Split each bead's request share across the same estimated tool buckets."""

import sqlite3
from collections import Counter

from hive.bead_assignment import Assignment
from hive.identity import ThreadId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import Quote, dollars
from hive.tool_allocation import Charge, split
from hive.tool_report import report
from hive.usage_store import row


def allocations(connection: sqlite3.Connection) -> dict[str, tuple[Charge, ...]]:
    fetched: object = connection.execute(
        "SELECT r.task,r.response,e.quote FROM responses r JOIN response_estimates e ON e.response=r.response AND e.tier=r.modifier_key WHERE r.host='claude'"
    ).fetchall()
    quotes: dict[str, dict[str, Quote]] = {}
    for value in sequence(fetched, "tool prices"):
        task, response, raw = row(value, 3)
        quotes.setdefault(string(task, "task"), {})[string(response, "response")] = (
            Quote.read(parse(string(raw, "quote")))
        )
    result: dict[str, tuple[Charge, ...]] = {}
    for task, prices in quotes.items():
        result.update(report(connection, ThreadId(task), prices).requests)
    return result


def breakdown(
    assignments: tuple[Assignment, ...],
    bead: str,
    allocated: dict[str, tuple[Charge, ...]],
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
