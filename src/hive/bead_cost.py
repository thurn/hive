"""Per-bead reports and a shared reconciliation over retained request prices."""

from collections import Counter
from dataclasses import replace

from hive.bead_assignment import Assignment, Evidence, hour
from hive.bead_queries import BEAD
from hive.bead_requests import owners, prepare, requests
from hive.errors import ErrorCode, HiveError
from hive.identity import PricingTier
from hive.jsonvalue import sequence, string
from hive.pricing import dollars
from hive.request_coverage import Coverage
from hive.request_coverage import read as coverage
from hive.usage_store import UsageStore, row


def reconcile(assignments: tuple[Assignment, ...]) -> dict[str, object]:
    threads: dict[str, Counter[str]] = {}
    beads: Counter[str] = Counter()
    for assignment in assignments:
        request = assignment.request
        totals = threads.setdefault(request.thread, Counter())
        if request.amount is None:
            totals["unpriced_requests"] += 1
            continue
        totals["total"] += request.amount
        if assignment.kind in {"owned", "shared"}:
            for interval, amount in assignment.shares:
                beads[interval.bead] += amount
                totals["attributed"] += amount
        else:
            totals[assignment.kind] += request.amount
    totals: Counter[str] = Counter()
    for thread in threads.values():
        totals.update(thread)
    difference = (
        totals["total"]
        - sum(beads.values())
        - totals["unowned"]
        - totals["unattributable"]
    )
    return {
        "code": "CostReconciliation",
        "balanced": difference == 0,
        "difference_usd": dollars(difference),
        "thread_total_usd": dollars(totals["total"]),
        "attributed_usd": dollars(sum(beads.values())),
        "unowned_usd": dollars(totals["unowned"]),
        "unattributable_usd": dollars(totals["unattributable"]),
        "unpriced_requests": totals["unpriced_requests"],
        "threads": [
            {
                "thread": task,
                "total_usd": dollars(amount["total"]),
                "attributed_usd": dollars(amount["attributed"]),
                "unowned_usd": dollars(amount["unowned"]),
                "unattributable_usd": dollars(amount["unattributable"]),
                "unpriced_requests": amount["unpriced_requests"],
            }
            for task, amount in sorted(threads.items())
        ],
        "beads": [
            {"bead": bead, "attributed_usd": dollars(amount)}
            for bead, amount in sorted(beads.items())
        ],
        "meaning": "Bookkeeping consistency, not attribution accuracy.",
    }


def report(
    store: UsageStore,
    bead: str | None,
    tier: PricingTier | None = None,
    *,
    history_error: str | None = None,
) -> dict[str, object]:
    if bead is not None and BEAD.fullmatch(bead) is None:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid bead ID")
    selected = tier or PricingTier.STANDARD
    unretained = prepare(store, selected, bead)
    if unretained:
        return {
            "code": "CostReconciliation" if bead is None else "BeadCost",
            "bead": bead,
            "unretained_estimates": unretained,
            "reason": "Price retention is incomplete",
            "balanced": None,
        }
    with store.connect(write=False) as connection:
        evidence = Evidence.read(connection)
        if history_error is not None:
            evidence = replace(evidence, caught_up=False)
        from hive.tool_beads import allocations, breakdown

        scope = owners(connection, bead)
        allocated = allocations(connection, scope) if scope is not None else {}
        reports = {
            task: coverage(connection, store.path.parent, task, selected)
            for task in scope or ()
        }
        assignments = tuple(
            evidence.assign(request)
            for request in requests(connection, selected, scope)
        )
        if bead is None:
            return {
                **reconcile(assignments),
                "bead_events_caught_up": evidence.caught_up,
                "unretained_estimates": 0,
                "pricing_tier_assumption": selected,
            }
        metadata: object = connection.execute(
            "SELECT status,reason,deleted,renamed_to FROM bead_replays WHERE bead=?",
            (bead,),
        ).fetchone()
        status, reason, deleted, renamed = (
            ("unknown", "No cached ownership history", 0, None)
            if metadata is None
            else row(metadata, 4)
        )
        creator_rows: object = connection.execute(
            "SELECT task FROM collection_links WHERE bead=? AND relation='creator' ORDER BY task",
            (bead,),
        ).fetchall()
        threads = scope or ()
        creators = [
            string(row(value, 1)[0], "creator")
            for value in sequence(creator_rows, "creators")
        ]
    intervals: Counter[int] = Counter()
    hosts: dict[int, str] = {}
    dimensions: dict[str, Counter[str | None]] = {
        name: Counter()
        for name in ("host", "model", "hour", "agent", "skill", "query_source")
    }
    total = shared = near = 0
    for assignment in assignments:
        request = assignment.request
        for interval, amount in assignment.shares:
            if interval.bead != bead:
                continue
            total += amount
            intervals[interval.ordinal] += amount
            hosts[interval.ordinal] = request.host
            shared += int(assignment.kind == "shared")
            near += int(assignment.near_boundary)
            for name, value in (
                ("host", str(request.host)),
                ("model", request.model),
                ("hour", hour(request.at)),
                ("agent", request.agent),
                ("skill", request.skill),
                ("query_source", request.query_source),
            ):
                dimensions[name][value] += amount

    def grouped(name: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for key, amount in sorted(
            dimensions[name].items(), key=lambda item: item[0] or ""
        ):
            entry: dict[str, object] = {name: key, "usd": dollars(amount)}
            result.append(entry)
        return result

    return {
        "code": "BeadCost",
        "bead": bead,
        "attributed_usd": dollars(total),
        "intervals": [
            {
                "thread": interval.thread,
                "host": hosts.get(
                    interval.ordinal,
                    reports.get(
                        interval.thread, Coverage(None, False, True, False)
                    ).host,
                ),
                "start": interval.start.isoformat(),
                "end": None if interval.end is None else interval.end.isoformat(),
                "usd": dollars(intervals[interval.ordinal]),
                "deleted": interval.deleted,
            }
            for interval in evidence.intervals
            if interval.bead == bead
        ],
        **{"by_" + name: grouped(name) for name in dimensions},
        "by_tool": breakdown(assignments, bead, allocated),
        "tool_allocation": "Estimated transcript tool shares; Codex and event-only costs remain explicit buckets.",
        "shared_requests": shared,
        "near_boundary_requests": near,
        "bead_events_caught_up": evidence.caught_up,
        "interval_status": status if evidence.caught_up else "unknown",
        "interval_reason": (
            reason
            if evidence.caught_up
            else history_error or "Ownership history is not caught up"
        ),
        "deleted": bool(deleted),
        "renamed_to": renamed,
        "coverage": {
            "threads_with_unpriced": sorted(
                task for task in threads if reports[task].unpriced
            ),
            "threads_with_source_gaps": sorted(
                task for task in threads if reports[task].source_gaps
            ),
            "threads_without_complete_estimate": sorted(
                task for task in threads if not reports[task].complete
            ),
        },
        "not_captured": "Codex spawned reviewers without their own intervals are included through their root parent; unrelated threads that never held the bead are excluded.",
        "creator_threads": creators,
        "unretained_estimates": 0,
        "pricing_tier_assumption": selected,
    }
