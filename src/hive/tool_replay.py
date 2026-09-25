"""Immutable context snapshots and pure tool allocation, without presentation work."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from hive.errors import ErrorCode, HiveError
from hive.identity import ThreadId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import PricedUsage
from hive.tool_allocation import Charge, Segment, TokenShare, basis, price
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
class RequestContext:
    response: str
    owner: str
    usage: Tokens | None
    at: datetime
    measured: bool
    previous: str | None
    reset: bool
    missing: bool
    parts: tuple[Part, ...]
    blocks: tuple[Part, ...]


@dataclass(frozen=True)
class Inputs:
    task: ThreadId
    requests: tuple[RequestContext, ...]
    names: tuple[tuple[str, str], ...]
    agents: tuple[tuple[str, str | None], ...]
    prices: tuple[tuple[str, PricedUsage], ...]


@dataclass(frozen=True)
class Replay:
    bases: tuple[tuple[str, tuple[TokenShare, ...]], ...]
    requests: tuple[tuple[str, tuple[Charge, ...]], ...]
    counts: tuple[tuple[str, int], ...]
    samples: tuple[tuple[str, int, int], ...]
    tool_calls: tuple[tuple[str, int], ...]
    tool_methods: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    agent_totals: tuple[tuple[str, int], ...]


def read(
    connection: sqlite3.Connection,
    task: ThreadId,
    quotes: dict[str, PricedUsage],
    *,
    unindexed: tuple[str, ...] | None = None,
) -> Inputs:
    """Read full context chains, or explicitly independent unindexed observations."""
    scope = (
        ""
        if unindexed is None
        else "AND r.response IN (SELECT value FROM json_each(?)) "
    )
    parameters: tuple[object, ...] = (
        (task,) if unindexed is None else (task, json.dumps(unindexed))
    )
    fetched: object = connection.execute(
        "SELECT r.response,r.agent,r.usage,r.observed,r.flags,a.previous,a.reset,a.ordinal FROM responses r "
        "LEFT JOIN allocation_responses a ON a.response=r.response WHERE r.task=? AND r.host='claude' "
        + scope
        + "ORDER BY COALESCE(a.ordinal,9223372036854775807),r.observed,r.response",
        parameters,
    ).fetchall()
    requests: list[RequestContext] = []
    for raw in sequence(fetched, "allocation requests"):
        identity, agent, usage, at, flags, previous, reset, ordinal = row(raw, 8)
        response = string(identity, "response")
        if unindexed is not None and ordinal is not None:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Indexed context requires full replay"
            )
        requests.append(
            RequestContext(
                response,
                "" if agent is None else string(agent, "agent"),
                None if usage is None else tokens(parse(string(usage, "usage"))),
                timestamp(at),
                "thinking_unmeasured"
                not in sequence(parse(string(flags, "flags")), "flags"),
                None if previous is None else string(previous, "previous"),
                bool(reset),
                ordinal is None,
                read_parts(connection, response, output=False),
                read_parts(connection, response, output=True),
            )
        )
    named: object = connection.execute(
        "SELECT b.ref,b.name FROM response_blocks b JOIN responses r ON r.response=b.response WHERE r.task=? AND b.kind='tool_use' ORDER BY b.ref,b.name",
        (task,),
    ).fetchall()
    agents: object = connection.execute(
        "SELECT agent,tool_use_id FROM claude_agents WHERE task=? ORDER BY agent",
        (task,),
    ).fetchall()
    return Inputs(
        task,
        tuple(requests),
        tuple(
            (string(row(v, 2)[0], "tool ref"), string(row(v, 2)[1], "tool name"))
            for v in sequence(named, "tools")
        ),
        tuple(
            (
                string(row(v, 2)[0], "agent"),
                None if row(v, 2)[1] is None else string(row(v, 2)[1], "spawn tool"),
            )
            for v in sequence(agents, "agents")
        ),
        tuple(sorted(quotes.items())),
    )


def replay(data: Inputs) -> Replay:
    """Pure integer allocation; callers may reuse only an identical input snapshot."""
    quotes = dict(data.prices)
    names = dict(data.names)
    contexts: dict[str, Context] = {}
    counts: Counter[str] = Counter()
    samples: list[tuple[str, int, int]] = []
    requests: list[tuple[str, tuple[Charge, ...]]] = []
    bases: list[tuple[str, tuple[TokenShare, ...]]] = []
    tool_calls: dict[str, set[str]] = {}
    tool_methods: dict[str, Counter[str]] = {}
    agent_totals: Counter[str] = Counter()
    for value in data.requests:
        (
            response,
            owner,
            usage,
            observed,
            measured,
            previous,
            reset,
            missing,
            parts,
            blocks,
        ) = (
            value.response,
            value.owner,
            value.usage,
            value.at,
            value.measured,
            value.previous,
            value.reset,
            value.missing,
            value.parts,
            value.blocks,
        )
        if usage is None:
            contexts.pop(owner, None)
            continue
        counts["thinking_unmeasured"] += int(not measured)
        prior = contexts.get(owner)
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
        shares = basis(usage, segments, blocks, names, thinking_measured=measured)
        bases.append((response, shares))
        quoted = quotes.get(response)
        if quoted is None:
            continue
        charges = price(quoted, shares)
        requests.append((response, charges))
        agent_totals[owner] += quoted.amount
    return Replay(
        tuple(bases),
        tuple(requests),
        tuple(counts.items()),
        tuple(samples),
        tuple((name, len(calls)) for name, calls in tool_calls.items()),
        tuple((name, tuple(methods.items())) for name, methods in tool_methods.items()),
        tuple(agent_totals.items()),
    )
