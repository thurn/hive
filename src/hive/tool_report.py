"""Present a tool replay; its immutable snapshot can also supply retention."""

import sqlite3
from collections import Counter
from fractions import Fraction
from statistics import median

from hive.identity import ThreadId
from hive.pricing import PricedUsage, dollars
from hive.tool_replay import read, replay


def accuracy(samples: list[tuple[str, int, int]]) -> dict[str, object] | None:
    if len(samples) < 20:
        return None
    totals: dict[str, tuple[int, int]] = {}
    for kind, size, count in samples:
        old_size, old_count = totals.get(kind, (0, 0))
        totals[kind] = old_size + size, old_count + count
    ratios = {kind: Fraction(count, size) for kind, (size, count) in totals.items()}
    errors = sorted(
        float(abs(ratios[kind] * size - count) / count) for kind, size, count in samples
    )
    return {
        "samples": len(samples),
        "median_relative_error": median(errors),
        "p90_relative_error": errors[(9 * len(errors) + 9) // 10 - 1],
        "tokens_per_byte": {
            kind: float(ratio) for kind, ratio in sorted(ratios.items())
        },
        "meaning": "In-sample single-part fit; not a guarantee for mixed parts.",
    }


def report(
    connection: sqlite3.Connection, task: ThreadId, quotes: dict[str, PricedUsage]
) -> dict[str, object]:
    data = read(connection, task, quotes)
    result = replay(data)
    requests = result.requests
    counts = Counter(dict(result.counts))
    names = dict(data.names)
    tool_calls = dict(result.tool_calls)
    tool_methods = {
        name: Counter(dict(methods)) for name, methods in result.tool_methods
    }
    agent_totals = dict(result.agent_totals)
    buckets: Counter[str] = Counter()
    tools: dict[str, Counter[str]] = {}
    for _, charges in requests:
        for charge in charges:
            buckets[charge.bucket] += charge.amount
            if charge.tool is not None:
                tools.setdefault(charge.tool, Counter())[charge.phase] += charge.amount
    delegated: Counter[str] = Counter()
    for agent, tool in data.agents:
        if tool is not None and tool in names:
            delegated[names[tool]] += agent_totals.get(agent, 0)
    expected = sum(quote.amount for quote in quotes.values())
    difference = expected - sum(buckets.values())
    details: dict[str, object] = {
        "by_tool": [
            {
                "tool": name,
                "calls": tool_calls.get(name, 0),
                "invocation_usd": dollars(amount["invocation"]),
                "carrying_usd": dollars(amount["carrying"]),
                "usd": dollars(sum(amount.values())),
                "exact_segments": tool_methods.get(name, Counter())["exact_segments"],
                "byte_split_segments": tool_methods.get(name, Counter())[
                    "byte_split_segments"
                ],
                "delegated_subagent_usd": dollars(delegated[name]),
            }
            for name, amount in sorted(tools.items())
        ],
        "allocation_buckets": [
            {"bucket": name, "usd": dollars(amount)}
            for name, amount in sorted(buckets.items())
        ],
        "unallocated_usd": dollars(difference),
        "allocation_error": (
            None
            if difference == 0
            else "Bucket sum differs from priced transcript total"
        ),
        "allocation": "estimated: segment sizes measured from usage; multi-part segments split by bytes; 1h-before-5m write band order assumed",
        "allocation_consistency": "Bookkeeping only, not allocation accuracy; event-only requests remain outside transcript allocation.",
        "byte_split_error": accuracy(list(result.samples)),
        "allocation_methods": dict(
            sorted(
                Counter(
                    charge.method for _, charges in requests for charge in charges
                ).items()
            )
        ),
        **{
            name: counts[name]
            for name in (
                "context_resets",
                "suspected_prefix_rewrite",
                "thinking_unmeasured",
                "unknown_attachment_kinds",
                "allocation_missing_requests",
            )
        },
    }
    return details
