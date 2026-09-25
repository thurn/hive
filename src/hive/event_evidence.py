"""Event coverage is evidence, separate from transcript pricing and attribution."""

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hive.claude_usage import Modifiers
from hive.errors import HiveError
from hive.identity import ModelId, ResponseId, ThreadId
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.pricing import Priced
from hive.request_pricing import estimate_claude
from hive.usage import timestamp, tokens
from hive.usage_store import row


@dataclass(frozen=True)
class EventEvidence:
    counts: tuple[tuple[str, int], ...]
    versions: tuple[tuple[str, tuple[int, int]], ...]
    groups: tuple[tuple[str, tuple[int, int, int]], ...]
    assumptions: tuple[str, ...]
    side_amount: int
    host_micros: int
    gaps: int
    sequence_conflicts: int
    failures: int
    api_errors: int
    listener: int
    coverage: float | None
    conditions: tuple[str, ...]
    host_matches: bool | None
    amount: int

    @property
    def unpriced(self) -> int:
        counts = dict(self.counts)
        return counts.get("only", 0) - counts.get("priced_only", 0)

    @property
    def complete(self) -> bool:
        return not self.conditions


def sequences(
    connection: sqlite3.Connection, task: ThreadId
) -> tuple[int, int, datetime | None, datetime | None]:
    fetched: object = connection.execute(
        "SELECT occurred,sequence FROM claude_event_sequences WHERE task=? ORDER BY occurred,sequence",
        (task,),
    ).fetchall()
    groups: dict[str, set[int]] = {}
    process = "unanchored"
    first: datetime | None = None
    last: datetime | None = None
    conflicts = 0
    for raw in sequence(fetched, "event sequences"):
        at, number = row(raw, 2)
        observed = timestamp(at)
        number = integer(number, "event sequence")
        if number == 0:
            process = string(at, "process sequence start")
        numbers = groups.setdefault(process, set())
        conflicts += int(number in numbers)
        numbers.add(number)
        first = observed if first is None else min(first, observed)
        last = observed if last is None else max(last, observed)
    return (
        sum(max(numbers) + 1 - len(numbers) for numbers in groups.values()),
        conflicts,
        first,
        last,
    )


def rejections(state: Path, first: datetime | None, last: datetime | None) -> int:
    if first is None or last is None:
        return 0
    count = 0
    # Include final exporter/body latency; absence of a log is ordinary before
    # the first 503. Bad accounting evidence is conservatively a rejection.
    try:
        with (state / "otlp-spool/rejections.log").open("rb") as stream:
            for _ in range(100_000):
                line = stream.readline(4096)
                if not line:
                    return count
                value = record(parse(line.decode("utf-8")), "listener rejection")
                if first <= timestamp(value.get("at")) <= last + timedelta(seconds=30):
                    count += integer(value.get("count"), "listener rejection count")
            return count + 1
    except FileNotFoundError:
        return 0
    except (HiveError, OSError, UnicodeError):
        return count + 1


def read(
    connection: sqlite3.Connection,
    state: Path,
    task: ThreadId,
    fresh: list[tuple[str, str, str]],
    *,
    observed: int,
    priced: int,
    amount: int,
    partial: int,
    health: dict[str, object],
    host_totals: dict[str, object],
) -> EventEvidence:
    fetched: object = connection.execute(
        "SELECT v.response,v.request_id,v.model,v.usage,v.modifiers,v.micros,v.query_source,v.version,v.flags,e.quote,"
        "r.response,r.task,r.model,r.usage FROM claude_request_events v "
        "LEFT JOIN response_estimates e ON e.response=v.response AND e.tier=v.modifier_key "
        "LEFT JOIN responses r ON r.response=v.request_id AND r.host='claude' WHERE v.task=?",
        (task,),
    ).fetchall()
    counts: Counter[str] = Counter()
    versions: dict[str, tuple[int, int]] = {}
    groups: dict[str, tuple[int, int, int]] = {}
    assumptions: set[str] = set()
    side_amount = 0
    host_micros = 0
    for raw in sequence(fetched, "request events"):
        (
            response,
            request_id,
            model,
            usage,
            modifiers,
            micros,
            source,
            version,
            flags,
            cached,
            matched,
            thread,
            transcript_model,
            transcript_usage,
        ) = row(raw, 14)
        if request_id is None:
            counts["unjoinable"] += 1
            continue
        counts["requests"] += 1
        host_micros += integer(micros, "event host cost")
        name = "unknown" if version is None else string(version, "Claude version")
        total, joined = versions.get(name, (0, 0))
        versions[name] = total + 1, joined + int(matched is not None and thread == task)
        if matched is not None:
            counts["matched"] += int(thread == task)
            if thread != task or model != transcript_model or transcript_usage is None:
                counts["mismatch"] += 1
            else:
                a = tokens(parse(string(usage, "event usage")))
                b = tokens(parse(string(transcript_usage, "transcript usage")))
                if (
                    a.input,
                    a.cached_input,
                    a.cache_write_input + a.cache_write_1h_input,
                    a.output,
                ) != (
                    b.input,
                    b.cached_input,
                    b.cache_write_input + b.cache_write_1h_input,
                    b.output,
                ):
                    counts["mismatch"] += 1
            continue
        counts["only"] += 1
        parsed = tokens(parse(string(usage, "event usage")))
        outcome = estimate_claude(
            fresh,
            ResponseId(string(response, "event response")),
            ModelId(string(model, "event model")),
            Modifiers.read(parse(string(modifiers, "event modifiers"))),
            parsed,
            cached,
        )
        quoted = outcome.usage if isinstance(outcome, Priced) else None
        label = "unknown" if source is None else string(source, "query source")
        total, valued, subtotal = groups.get(label, (0, 0, 0))
        if quoted is not None:
            counts["priced_only"] += 1
            side_amount += quoted.amount
            subtotal += quoted.amount
            valued += 1
        else:
            counts[
                outcome.reason if not isinstance(outcome, Priced) else "unpriced_event"
            ] += 1
        groups[label] = total + 1, valued, subtotal
        assumptions.update(
            string(flag, "event flag")
            for flag in sequence(parse(string(flags, "event flags")), "event flags")
        )
    gaps, sequence_conflicts, first, last = sequences(connection, task)
    rejected: object = connection.execute(
        "SELECT COUNT(*) FROM claude_event_gaps WHERE task=? OR "
        "(task IS NULL AND observed>=? AND observed<=?)",
        (
            task,
            first.isoformat() if first else None,
            (last + timedelta(seconds=30)).isoformat() if last else None,
        ),
    ).fetchone()
    failures = integer(row(rejected, 1)[0], "event rejected records")
    errors: object = connection.execute(
        "SELECT COUNT(*) FROM claude_request_errors WHERE task=?", (task,)
    ).fetchone()
    listener = rejections(state, first, last)
    coverage = counts["matched"] / observed if observed else None
    conditions: list[str] = []
    if coverage != 1:
        conditions.append("incomplete_event_coverage")
    for failed, label in (
        (gaps > 0, "event_sequence_gaps"),
        (sequence_conflicts > 0, "conflicting_event_sequences"),
        (counts["unjoinable"] > 0, "unjoinable_events"),
        (listener > 0, "listener_rejections"),
        (failures > 0, "rejected_records"),
        (counts["mismatch"] > 0, "event_token_mismatches"),
        (any(joined == 0 for _, joined in versions.values()), "join_version_failure"),
        (
            observed != priced or counts["only"] != counts["priced_only"],
            "unpriced_requests",
        ),
        (partial > 0, "possibly_partial_output"),
        (
            health.get("remaining_bytes") is None
            or bool(health.get("parse_gaps"))
            or bool(health.get("source_error"))
            or bool(health.get("remaining_bytes"))
            or bool(health.get("incomplete_tail")),
            "incomplete_transcript_collection",
        ),
    ):
        if failed:
            conditions.append(label)
    if any((state / "otlp-spool").glob("*.json")):
        conditions.append("pending_event_spool")
    if host_totals.get("host_reported_reason") == "negative_difference":
        conditions.append("host_total_disagreement")
    host = host_totals.get("host_reported")
    host_matches: bool | None = None
    if host is not None:
        host_data = record(host, "host total")
        numerator, denominator = Decimal(
            string(host_data["usd"], "host dollars")
        ).as_integer_ratio()
        host_matches = (
            abs(numerator * 10**12 - host_micros * 1_000_000 * denominator)
            <= counts["requests"] * 1_000_000 * denominator
        )
        if not host_matches:
            conditions.append("host_total_disagreement")
        if host_data.get("has_unknown_model_cost"):
            conditions.append("incomplete_host_total")
    return EventEvidence(
        tuple(counts.items()),
        tuple(versions.items()),
        tuple(groups.items()),
        tuple(sorted(assumptions)),
        side_amount,
        host_micros,
        gaps,
        sequence_conflicts,
        failures,
        integer(row(errors, 1)[0], "API error count"),
        listener,
        coverage,
        tuple(conditions),
        host_matches,
        amount,
    )
