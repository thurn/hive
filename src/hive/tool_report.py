"""Replay independent agent contexts against the report's exact quote snapshot."""

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from statistics import median

from hive.identity import ThreadId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import Quote, dollars
from hive.tool_allocation import Charge, Segment, allocate
from hive.tool_parts import Part
from hive.tool_store import read_parts
from hive.usage import Tokens, timestamp, tokens
from hive.usage_store import row


@dataclass(frozen=True)
class Context:
    response: str
    usage: Tokens
    at: datetime
    segments: tuple[Segment, ...]
    ttl: int | None
    thinking_measured: bool


@dataclass(frozen=True)
class Allocation:
    requests: tuple[tuple[str, tuple[Charge, ...]], ...]
    details: dict[str, object]


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
    connection: sqlite3.Connection, task: ThreadId, quotes: dict[str, Quote]
) -> Allocation:
    fetched: object = connection.execute(
        "SELECT r.response,r.agent,r.usage,r.observed,r.flags,a.previous,a.reset,a.ordinal FROM responses r "
        "LEFT JOIN allocation_responses a ON a.response=r.response WHERE r.task=? AND r.host='claude' "
        "ORDER BY COALESCE(a.ordinal,9223372036854775807),r.observed,r.response",
        (task,),
    ).fetchall()
    named: object = connection.execute(
        "SELECT b.ref,b.name FROM response_blocks b JOIN responses r ON r.response=b.response WHERE r.task=? AND b.kind='tool_use'",
        (task,),
    ).fetchall()
    names = {
        string(row(value, 2)[0], "tool ref"): string(row(value, 2)[1], "tool name")
        for value in sequence(named, "tools")
    }
    contexts: dict[str, Context] = {}
    counts: Counter[str] = Counter()
    samples: list[tuple[str, int, int]] = []
    requests: list[tuple[str, tuple[Charge, ...]]] = []
    tool_calls: dict[str, set[str]] = {}
    tool_methods: dict[str, Counter[str]] = {}
    agent_totals: Counter[str] = Counter()
    for value in sequence(fetched, "allocation requests"):
        identity, agent, raw_usage, at, raw_flags, previous, reset, ordinal = row(
            value, 8
        )
        response = string(identity, "response")
        owner = "" if agent is None else string(agent, "agent")
        if raw_usage is None:
            contexts.pop(owner, None)
            continue
        usage = tokens(parse(string(raw_usage, "usage")))
        observed = timestamp(at)
        flags = sequence(parse(string(raw_flags, "flags")), "flags")
        measured = "thinking_unmeasured" not in flags
        counts["thinking_unmeasured"] += int(not measured)
        prior = contexts.get(owner)
        parts = read_parts(connection, response, output=False)
        blocks = read_parts(connection, response, output=True)
        missing = ordinal is None
        counts["allocation_missing_requests"] += int(missing)
        broken = bool(reset) or (
            prior is not None
            and (
                usage.input < prior.usage.input
                or previous != prior.response
                or observed < prior.at
            )
        )
        suspected = (
            prior is not None
            and not broken
            and prior.ttl is not None
            and usage.cached_input < prior.usage.input
            and 0 <= (observed - prior.at).total_seconds() < prior.ttl
        )
        counts["context_resets"] += int(broken)
        counts["suspected_prefix_rewrite"] += int(suspected)
        if prior is None or broken or suspected or missing:
            base = (
                "rewritten_context"
                if broken or suspected or missing or previous is not None
                else "base_context"
            )
            segments = (Segment(usage.input, (Part(base, None, None, 1),)),)
            ttl: int | None = None
        else:
            delta = usage.input - prior.usage.input
            segment = Segment(
                delta,
                parts,
                prior.usage.reasoning_output if prior.thinking_measured else 0,
            )
            segments = prior.segments + ((segment,) if delta else ())
            ttl = prior.ttl
            if (
                delta
                and len(parts) == 1
                and parts[0].size
                and parts[0].kind not in {"thinking", "oversized"}
            ):
                samples.append((parts[0].kind, parts[0].size, delta))
            if delta:
                for part in parts:
                    if part.kind in {"tool_use", "tool_result"}:
                        name = part.name or names.get(part.ref or "", "unknown")
                        methods = tool_methods.setdefault(name, Counter())
                        methods[
                            (
                                "exact_segments"
                                if len(parts) == 1
                                else "byte_split_segments"
                            )
                        ] += 1
        if usage.cache_write_input:
            ttl = 300
        elif usage.cache_write_1h_input:
            ttl = min(ttl or 3600, 3600)
        contexts[owner] = Context(response, usage, observed, segments, ttl, measured)
        for part in blocks:
            if part.kind == "tool_use":
                tool_calls.setdefault(part.name or "unknown", set()).add(
                    part.ref or response
                )
        counts["unknown_attachment_kinds"] += sum(
            part.kind == "attachment"
            and part.name not in {"image", "document", "file", "directory", "text"}
            for part in parts
        )
        quoted = quotes.get(response)
        if quoted is None:
            continue
        charges = allocate(
            usage, quoted, segments, blocks, names, thinking_measured=measured
        )
        requests.append((response, charges))
        agent_totals[owner] += quoted.amount
    buckets: Counter[str] = Counter()
    tools: dict[str, Counter[str]] = {}
    for _, charges in requests:
        for charge in charges:
            buckets[charge.bucket] += charge.amount
            if charge.tool is not None:
                tools.setdefault(charge.tool, Counter())[charge.phase] += charge.amount
    delegated: Counter[str] = Counter()
    metadata: object = connection.execute(
        "SELECT agent,tool_use_id FROM claude_agents WHERE task=?", (task,)
    ).fetchall()
    for value in sequence(metadata, "delegated agents"):
        agent, tool = row(value, 2)
        if tool is not None and string(tool, "spawn tool") in names:
            delegated[names[string(tool, "spawn tool")]] += agent_totals[
                string(agent, "agent")
            ]
    expected = sum(quote.amount for quote in quotes.values())
    difference = expected - sum(buckets.values())
    details: dict[str, object] = {
        "by_tool": [
            {
                "tool": name,
                "calls": len(tool_calls.get(name, set())),
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
        "byte_split_error": accuracy(samples),
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
    return Allocation(tuple(requests), details)
