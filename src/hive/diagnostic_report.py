"""Deterministic diagnostic classification over stored metadata and retained prices."""

import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from hive.diagnostic_roles import at as role_at
from hive.jsonvalue import integer, parse, sequence, string
from hive.pricing import read_pricing
from hive.usage import timestamp
from hive.usage_store import row

WAITING: frozenset[str] = frozenset({"tg_wait", "sleep", "monitor"})


def cache_amount(connection: sqlite3.Connection, response: str) -> int | None:
    value: object = connection.execute(
        "SELECT r.cache_write,r.cache_write_1h,r.cached,p.input,e.quote FROM responses r JOIN allocation_responses a ON a.response=r.response JOIN responses p ON p.response=a.previous LEFT JOIN response_estimates e ON e.response=r.response AND e.tier=r.modifier_key WHERE r.response=?",
        (response,),
    ).fetchone()
    if value is None:
        return None
    write, hour, read, previous, quote = row(value, 5)
    if quote is None:
        return None
    price = read_pricing(parse(string(quote, "retained quote")))
    rewrite = max(0, integer(previous, "previous input") - integer(read, "read tokens"))
    long_write = min(rewrite, integer(hour, "hour writes"))
    short_write = min(rewrite - long_write, integer(write, "short writes"))
    return long_write * (
        price.card.rates.cache_write_1h - price.card.rates.cached
    ) + short_write * (price.card.rates.cache_write - price.card.rates.cached)


def events(connection: sqlite3.Connection, thread: str) -> list[dict[str, object]]:
    cursor = connection.execute(
        "SELECT id,host,thread,agent,kind,at,until,amount_picos,ref,file,offset FROM session_events WHERE thread=? ORDER BY at,id",
        (thread,),
    )
    result: list[dict[str, object]] = []
    for value in sequence(cursor.fetchall(), "session events"):
        values = row(value, 11)
        event: dict[str, object] = dict(
            zip(
                (
                    "id",
                    "host",
                    "thread",
                    "agent",
                    "kind",
                    "at",
                    "until",
                    "amount_picos",
                    "ref",
                    "file",
                    "offset",
                ),
                values,
                strict=True,
            )
        )
        if event["kind"] == "cache_rewrite" and isinstance(event["ref"], str):
            amount = cache_amount(connection, string(event["ref"], "request reference"))
            event["amount_picos"] = None if amount is None else str(amount)
        result.append(event)
    return result


def tools(connection: sqlite3.Connection, thread: str) -> list[dict[str, object]]:
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    baseline: object = connection.execute(
        "SELECT c.tool,c.command_kind,c.duration_ms FROM tool_calls c LEFT JOIN project_sessions s ON c.thread=s.thread WHERE c.duration_ms IS NOT NULL AND c.started_at>=? AND COALESCE(s.project,'')=COALESCE((SELECT project FROM project_sessions WHERE thread=?),'')",
        (cutoff, thread),
    ).fetchall()
    samples: dict[str, list[int]] = defaultdict(list)
    for value in sequence(baseline, "tool durations"):
        tool, kind, duration = row(value, 3)
        name = string(tool if kind == "other" else kind, "duration group")
        if name not in WAITING:
            samples[name].append(integer(duration, "duration"))
    thresholds = {
        name: sorted(values)[max(0, math.ceil(0.95 * len(values)) - 1)]
        for name, values in samples.items()
    }
    cursor = connection.execute(
        "SELECT host,thread,agent,call_id,tool,started_at,finished_at,duration_ms,status,input_hash8,input_bytes,result_bytes,command_kind,file,use_offset,result_offset FROM tool_calls WHERE thread=? ORDER BY started_at,call_id",
        (thread,),
    )
    result: list[dict[str, object]] = []
    runs: dict[str, tuple[tuple[str, str], list[int]]] = {}
    for value in sequence(cursor.fetchall(), "tool calls"):
        values = row(value, 16)
        call: dict[str, object] = dict(
            zip(
                (
                    "host",
                    "thread",
                    "agent",
                    "call_id",
                    "tool",
                    "started_at",
                    "finished_at",
                    "duration_ms",
                    "status",
                    "input_hash8",
                    "input_bytes",
                    "result_bytes",
                    "command_kind",
                    "file",
                    "use_offset",
                    "result_offset",
                ),
                values,
                strict=True,
            )
        )
        tool, kind = string(call["tool"], "tool"), string(call["command_kind"], "kind")
        name = tool if kind == "other" else kind
        duration = call["duration_ms"]
        call["waiting"] = kind in WAITING
        call["slow"] = (
            isinstance(duration, int)
            and kind not in WAITING
            and duration > max(60000, thresholds.get(name, duration))
        )
        call["retry_loop"] = False
        agent = string(call["agent"], "agent", empty=True)
        role, inherited = role_at(
            connection, thread, agent, string(call["started_at"], "start")
        )
        call["role"], call["role_inherited"] = role, inherited
        key = (tool, string(call["input_hash8"], "input hash"))
        previous, indices = runs.get(agent, (("", ""), []))
        if call["status"] == "error":
            indices = indices + [len(result)] if previous == key else [len(result)]
            runs[agent] = key, indices
            if len(indices) >= 3:
                call["retry_loop"] = True
                for index in indices[:-1]:
                    result[index]["retry_loop"] = True
        else:
            runs.pop(agent, None)
        result.append(call)
    grouped = command_rows(connection, thread)
    for call in result:
        key = (
            string(call["agent"], "agent", empty=True),
            string(call["call_id"], "call"),
        )
        call["commands"] = grouped.get(key, [])
    return result


def command_rows(
    connection: sqlite3.Connection, thread: str
) -> dict[tuple[str, str], list[dict[str, object]]]:
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    baseline: object = connection.execute(
        "SELECT d.command_kind,d.wall_ms FROM tool_commands d JOIN tool_calls c USING(thread,agent,call_id) LEFT JOIN project_sessions s ON c.thread=s.thread WHERE d.wall_ms IS NOT NULL AND c.started_at>=? AND COALESCE(s.project,'')=COALESCE((SELECT project FROM project_sessions WHERE thread=?),'')",
        (cutoff, thread),
    ).fetchall()
    samples: dict[str, list[int]] = defaultdict(list)
    for value in sequence(baseline, "command durations"):
        kind, duration = row(value, 2)
        samples[string(kind, "kind")].append(integer(duration, "duration"))
    thresholds = {
        name: sorted(values)[math.ceil(0.95 * len(values)) - 1]
        for name, values in samples.items()
    }
    values: object = connection.execute(
        "SELECT d.agent,d.call_id,d.ordinal,d.exit_code,d.wall_ms,d.command_hash8,d.command_kind FROM tool_commands d JOIN tool_calls c USING(thread,agent,call_id) WHERE d.thread=? ORDER BY c.started_at,c.call_id,d.ordinal",
        (thread,),
    ).fetchall()
    result: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    runs: dict[str, tuple[str, list[dict[str, object]]]] = {}
    for value in sequence(values, "command rows"):
        agent, call, ordinal, code, duration, digest, kind = row(value, 7)
        agent, call, kind, digest = (
            string(agent, "agent", empty=True),
            string(call, "call"),
            string(kind, "kind"),
            string(digest, "hash", empty=True),
        )
        item: dict[str, object] = dict(
            ordinal=ordinal,
            exit_code=code,
            wall_ms=duration,
            command_hash8=digest,
            command_kind=kind,
            waiting=kind in WAITING,
            retry_loop=False,
        )
        item["slow"] = (
            isinstance(duration, int)
            and kind not in WAITING
            and duration > max(60000, thresholds.get(kind, duration))
        )
        previous, run = runs.get(agent, ("", []))
        # Empty input hash means the native output could not be matched to a command.
        if code not in (None, 0) and digest not in ("", "e3b0c442"):
            run = run + [item] if previous == digest else [item]
            runs[agent] = digest, run
            if len(run) >= 3:
                for member in run:
                    member["retry_loop"] = True
        else:
            runs.pop(agent, None)
        result[(agent, call)].append(item)
    return dict(result)


def cache_event(
    connection: sqlite3.Connection, thread: str, response: str
) -> tuple[str, str] | None:
    value: object = connection.execute(
        "SELECT r.observed,r.input,r.cached,r.cache_write+r.cache_write_1h,p.observed,p.input,a.reset,r.agent FROM responses r JOIN allocation_responses a ON a.response=r.response JOIN responses p ON p.response=a.previous WHERE r.task=? AND r.response=?",
        (thread, response),
    ).fetchone()
    if value is None:
        return None
    observed, total, cached, written, before, previous, reset, agent = row(value, 8)
    if reset or integer(total, "input") < integer(previous, "previous input"):
        return "compaction", string(observed, "observation")
    ttl_value: object = connection.execute(
        "SELECT cache_write_1h FROM responses WHERE task=? AND COALESCE(agent,'')=? AND observed<=? AND (cache_write>0 OR cache_write_1h>0) ORDER BY observed DESC,response DESC LIMIT 1",
        (thread, agent or "", before),
    ).fetchone()
    if ttl_value is None:
        return None
    ttl = 3600 if integer(row(ttl_value, 1)[0], "hour cache") > 0 else 300
    if (
        integer(cached, "cache read") < integer(previous, "prior input")
        and integer(written, "cache write") > 0
        and (timestamp(observed) - timestamp(before)).total_seconds() > ttl
    ):
        return "cache_rewrite", string(observed, "observation")
    return None
