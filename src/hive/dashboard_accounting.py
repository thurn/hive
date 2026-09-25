"""All dashboard dollar splits use retained quotes and native interval evidence."""

import sqlite3
from collections import Counter
from dataclasses import dataclass, replace

from hive.bead_assignment import Assignment, Evidence
from hive.bead_requests import Request
from hive.dashboard_values import picos, rows
from hive.diagnostic_roles import at as role_at
from hive.identity import Host, ThreadId
from hive.jsonvalue import parse, record, sequence, string
from hive.pricing import dollars
from hive.tool_allocation import split
from hive.usage import timestamp

COMPONENTS: tuple[str, ...] = (
    "input",
    "cache_write_5m",
    "cache_write_1h",
    "cache_read",
    "output",
    "server_tools",
)


@dataclass(frozen=True)
class Fact:
    request: Request
    assignment: Assignment
    role: str
    inherited: bool
    fields: tuple[tuple[str, object], ...]

    def public(self, share: int | None) -> dict[str, object]:
        return {
            **dict(self.fields),
            "role": self.role,
            "role_inherited": self.inherited,
            "share_picos": None if share is None else str(share),
            "share_usd": None if share is None else dollars(share),
            "assignment": self.assignment.kind,
        }


def facts(
    connection: sqlite3.Connection,
    thread: str | None = None,
    *,
    evidence: Evidence | None = None,
    after: str = "",
    limit: int = -1,
    identities: tuple[str, ...] | None = None,
) -> tuple[Fact, ...]:
    evidence = evidence or Evidence.read(connection)
    if identities is not None:
        values = rows(
            connection,
            "SELECT * FROM request_detail WHERE (tier IS NULL OR tier='standard') AND response IN ("
            + ",".join("?" for _ in identities)
            + ") ORDER BY response",
            identities,
        )
    else:
        values = rows(
            connection,
            "SELECT * FROM request_detail WHERE (tier IS NULL OR tier='standard') "
            + ("AND thread=? " if thread is not None else "")
            + "AND response>? ORDER BY response LIMIT ?",
            ((thread,) if thread is not None else ()) + (after, limit),
        )
    result: list[Fact] = []
    for value in values:
        amount = None if value["usd"] is None else picos(value["usd"])
        request = Request(
            string(value["response"], "response"),
            ThreadId(string(value["thread"], "thread")),
            Host(string(value["host"], "host")),
            timestamp(value["observed_at"]),
            None if value["model"] is None else string(value["model"], "model"),
            None if value["agent"] is None else string(value["agent"], "agent"),
            None if value["skill"] is None else string(value["skill"], "skill"),
            (
                None
                if value["query_source"] is None
                else string(value["query_source"], "query source")
            ),
            string(value["source"], "source"),
            amount,
        )
        # Unpriced observations still disclose the cards whose coverage is missing.
        assignment = evidence.assign(replace(request, amount=amount or 0))
        role, inherited = role_at(
            connection, request.thread, request.agent or "", request.at.isoformat()
        )
        value["flags"] = sequence(parse(string(value["flags"], "flags")), "flags")
        visible = tuple((k, v) for k, v in value.items() if not k.startswith("_"))
        result.append(Fact(request, assignment, role, inherited, visible))
    return tuple(result)


def bead_share(fact: Fact, bead: str) -> int | None:
    return next(
        (
            share if fact.request.amount is not None else None
            for interval, share in fact.assignment.shares
            if interval.bead == bead
        ),
        None,
    )


def project(connection: sqlite3.Connection, thread: str) -> str:
    values = rows(
        connection,
        "SELECT COALESCE(s.project,c.project,'Other') AS project FROM project_sessions s LEFT JOIN cwd_projects c ON s.cwd=c.cwd WHERE s.thread=?",
        (thread,),
    )
    return "Other" if not values else string(values[0]["project"], "project")


def unattributable_projects(
    connection: sqlite3.Connection, evidence: Evidence, fact: Fact, fallback: str
) -> tuple[str, ...]:
    owner = (
        fact.request.agent
        if fact.request.host == Host.CODEX
        and fact.request.agent is not None
        and any(u.thread == fact.request.agent for u in evidence.unknown)
        else fact.request.thread
    )
    beads = sorted(
        {
            u.bead
            for u in evidence.unknown
            if u.thread == owner and (u.start is None or fact.request.at >= u.start)
        }
    )
    result: list[str] = []
    for bead in beads:
        values = rows(connection, "SELECT project FROM bead_rows WHERE bead=?", (bead,))
        result.append(
            "Other" if not values else string(values[0]["project"], "project")
        )
    return tuple(result) or (fallback,)


def breakdown(
    connection: sqlite3.Connection, selected: tuple[tuple[Fact, int | None], ...]
) -> dict[str, list[dict[str, object]]]:
    dimensions: dict[str, Counter[str]] = {
        name: Counter()
        for name in (
            "role",
            "thread",
            "subagent",
            "model",
            "token_category",
            "skill",
            "query_source",
            "tool",
        )
    }
    for fact, amount in selected:
        if amount is None:
            continue
        request = fact.request
        for dimension, label in (
            ("role", fact.role),
            ("thread", request.thread),
            ("subagent", request.agent or "Main"),
            ("model", request.model or "Unknown"),
            ("skill", request.skill or "Unspecified"),
            ("query_source", request.query_source or "Unspecified"),
        ):
            dimensions[dimension][label] += amount
        fields = dict(fact.fields)
        components = tuple(picos(fields.get("usd_" + c) or "0") for c in COMPONENTS)
        for name, share in zip(COMPONENTS, split(amount, components), strict=True):
            dimensions["token_category"][name] += share
        allocations = rows(
            connection,
            "SELECT bucket,tool_name,usd FROM tool_allocation WHERE response=? ORDER BY ordinal",
            (request.response,),
        )
        if allocations:
            for value, share in zip(
                allocations,
                split(amount, tuple(picos(v["usd"]) for v in allocations)),
                strict=True,
            ):
                label = string(value["tool_name"] or value["bucket"], "tool")
                dimensions["tool"][label] += share
        else:
            dimensions["tool"]["Unallocated"] += amount
    return {
        name: [
            record(dict(label=label, amount_picos=str(amount), usd=dollars(amount)))
            for label, amount in sorted(
                values.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]
        for name, values in dimensions.items()
    }
