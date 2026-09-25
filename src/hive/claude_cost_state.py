"""Keep process totals separate from request price evidence and attribution."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hive.errors import ErrorCode, HiveError
from hive.identity import ThreadId
from hive.jsonvalue import integer, sequence, string
from hive.pricing import dollars
from hive.usage import timestamp
from hive.usage_store import row


def process_start(value: object) -> datetime:
    if isinstance(value, int) and not isinstance(value, bool):
        # Claude's JavaScript process start is milliseconds since the epoch.
        try:
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                milliseconds=integer(value, "cost-state startTime")
            )
        except OverflowError as error:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Invalid cost-state startTime"
            ) from error
    return timestamp(value).astimezone(UTC)


def save(
    connection: sqlite3.Connection, task: ThreadId, raw: dict[str, object]
) -> None:
    try:
        start = process_start(raw.get("startTime"))
        observed = timestamp(raw.get("timestamp")).astimezone(UTC)
        value = raw.get("totalCostUSD")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HiveError(ErrorCode.INVALID_RECORD, "totalCostUSD must be numeric")
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise HiveError(ErrorCode.INVALID_RECORD, "Invalid totalCostUSD")
        unknown = raw.get("hasUnknownModelCost", False)
        if not isinstance(unknown, bool) or observed < start:
            raise HiveError(ErrorCode.INVALID_RECORD, "Invalid process total metadata")
        connection.execute(
            "INSERT INTO claude_cost_states(task,start,observed,usd,unknown_model) VALUES (?,?,?,?,?) "
            "ON CONFLICT(task,start) DO UPDATE SET observed=excluded.observed,usd=excluded.usd,unknown_model=excluded.unknown_model "
            "WHERE excluded.observed >= claude_cost_states.observed",
            (task, start.isoformat(), observed.isoformat(), str(amount), int(unknown)),
        )
    except HiveError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, f"Invalid cost-state: {error}"
        ) from error


def comparison(
    connection: sqlite3.Connection,
    task: ThreadId,
    amount: int,
    last_priced: datetime | None,
) -> dict[str, object]:
    values: object = connection.execute(
        "SELECT start,observed,usd,unknown_model FROM claude_cost_states WHERE task=?",
        (task,),
    ).fetchall()
    states = sequence(values, "cost-state segments")
    reason: str | None = None
    reported: dict[str, object] | None = None
    delta: int | None = None
    invalid: object = connection.execute(
        "SELECT 1 FROM gaps WHERE task=? AND file='' AND detail LIKE '%cost-state%' LIMIT 1",
        (task,),
    ).fetchone()
    pending: object = connection.execute(
        "SELECT 1 FROM sources WHERE task=? AND host='claude' "
        "AND (remaining IS NULL OR remaining<>0 OR incomplete=1 OR error IS NOT NULL) LIMIT 1",
        (task,),
    ).fetchone()
    if pending is not None:
        reason = "incomplete_transcript_collection"
    elif invalid is not None:
        reason = "invalid_cost_state"
    elif not states:
        reason = "no_cost_state"
    elif len(states) != 1:
        reason = "multiple_process_segments"
    elif last_priced is None:
        reason = "no_priced_requests"
    else:
        start, observed, usd, unknown = row(states[0], 4)
        # Read all timestamps as instants; ISO lexical ordering is unsafe when
        # source files use different UTC offsets.
        times: object = connection.execute(
            "SELECT observed FROM responses WHERE task=? AND host='claude'", (task,)
        ).fetchall()
        first = min(
            timestamp(row(value, 1)[0]) for value in sequence(times, "request times")
        )
        host_amount = Decimal(string(usd, "host total"))
        # Host totals originate in JS floats. Round down sub-picodollar tails
        # instead of overstating the unrecorded lower bound.
        numerator, denominator = host_amount.as_integer_ratio()
        difference = numerator * 10**12 - amount * denominator
        if timestamp(observed) <= last_priced:
            reason = "cost_state_before_last_request"
        elif process_start(start) > first:
            reason = "process_starts_after_first_request"
        elif difference < 0:
            reason = "negative_difference"
        else:
            delta = difference // denominator
            reported = {
                "usd": str(host_amount),
                "source": "cost-state",
                "scope": "latest process only",
                "observed": observed,
                "start_time": start,
                "has_unknown_model_cost": bool(integer(unknown, "unknown model flag")),
            }
    return {
        "host_reported": reported,
        "host_reported_reason": reason,
        "host_process_segments": len(states),
        "unrecorded_usd_lower_bound": None if delta is None else dollars(delta),
    }
