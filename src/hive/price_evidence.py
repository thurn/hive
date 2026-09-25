"""Keep retained Claude rates while monotonic observations gain output tokens."""

import json
import sqlite3

from hive.claude_usage import Modifiers
from hive.errors import ErrorCode, HiveError
from hive.identity import ResponseId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import ClaudePricing, PriceableModifiers, PricedUsage, read_pricing
from hive.usage import Tokens
from hive.usage_store import row


def usage_updates(
    connection: sqlite3.Connection, response: ResponseId, usage: Tokens
) -> list[tuple[str, str, str]]:
    fetched: object = connection.execute(
        "SELECT tier,quote FROM response_estimates WHERE response=?", (response,)
    ).fetchall()
    updates: list[tuple[str, str, str]] = []
    for raw in sequence(fetched, "retained response estimates"):
        key, value = row(raw, 2)
        quoted = read_pricing(parse(string(value, "retained price")))
        if (
            not isinstance(quoted, ClaudePricing)
            or quoted.modifiers.observed.key != key
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Retained Claude price identity disagrees"
            )
        updates.append(
            (
                json.dumps(PricedUsage(quoted, usage).value()),
                response,
                string(key, "modifier key"),
            )
        )
    return updates


def apply_updates(
    connection: sqlite3.Connection, updates: list[tuple[str, str, str]]
) -> None:
    connection.executemany(
        "UPDATE response_estimates SET quote=? WHERE response=? AND tier=?", updates
    )


def adopt_event_rates(
    connection: sqlite3.Connection,
    response: ResponseId,
    model: str,
    modifiers: Modifiers,
    usage: Tokens,
) -> None:
    """Replace event assumptions with transcript facts, retaining the old card."""
    from dataclasses import replace

    from hive.identity import ModelId
    from hive.pricing import Rates, claude_reason

    event: object = connection.execute(
        "SELECT e.quote,v.modifier_key FROM claude_request_events v JOIN response_estimates e "
        "ON v.response=e.response AND v.modifier_key=e.tier WHERE v.request_id=?",
        (response,),
    ).fetchone()
    if event is None:
        return
    value, old_key = row(event, 2)
    quoted = read_pricing(parse(string(value, "event price evidence")))
    if not isinstance(quoted, ClaudePricing):
        raise HiveError(ErrorCode.INVALID_RECORD, "Non-Claude event price")
    old = quoted.modifiers.observed
    if quoted.card.model != model:
        # This evidence priced an incorrect event model, not the observed
        # request. Keep the conflicting event for audit, discard its quote.
        connection.execute(
            "DELETE FROM response_estimates WHERE response=? AND tier=?",
            (response, old_key),
        )
        return
    if claude_reason(ModelId(model), modifiers) is not None:
        return
    if old_key != modifiers.key:
        existing: object = connection.execute(
            "SELECT 1 FROM response_estimates WHERE response=? AND tier=?",
            (response, modifiers.key),
        ).fetchone()
        if existing is not None:
            return

    def factor(value: Modifiers) -> int:
        return (2 if value.speed == "fast" else 1) * (
            11 if value.inference_geo == "us" else 10
        )

    old_factor = factor(old)
    new_factor = factor(modifiers)
    rates = Rates(
        *(
            rate * new_factor // old_factor
            for rate in (
                quoted.card.rates.input,
                quoted.card.rates.cached,
                quoted.card.rates.cache_write,
                quoted.card.rates.output,
                quoted.card.rates.cache_write_1h,
            )
        )
    )
    corrected = PricedUsage(
        replace(
            quoted,
            modifiers=PriceableModifiers.read(modifiers),
            card=replace(quoted.card, rates=rates),
        ),
        usage,
    )
    connection.execute(
        "INSERT INTO response_estimates(response,tier,quote) VALUES (?,?,?) "
        "ON CONFLICT(response,tier) DO UPDATE SET quote=excluded.quote",
        (response, modifiers.key, json.dumps(corrected.value())),
    )
