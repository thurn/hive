"""Persist exact rate evidence and report assumptions separately from observation."""

import json
from collections import Counter
from dataclasses import replace

from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, ModelId, PricingTier, ResponseId, UsdPicos
from hive.jsonvalue import integer, parse, sequence, string
from hive.pricing import Quote, dollars, quote
from hive.usage import Tokens, tokens
from hive.usage_store import UsageStore, row, source_status


def estimate(
    fresh: list[tuple[str, str, str]],
    response: ResponseId,
    model: ModelId,
    tier: PricingTier,
    usage: Tokens,
    cached: object,
) -> Quote | None:
    if cached is not None:
        result = Quote.read(parse(string(cached, "stored estimate")))
        if result.model != model or result.tier != tier:
            raise HiveError(ErrorCode.INVALID_RECORD, "Stored price identity disagrees")
        return result
    result = quote(model, tier, usage)
    if result is not None:
        fresh.append((response, tier, json.dumps(result.value())))
    return result


def retain(store: UsageStore, fresh: list[tuple[str, str, str]]) -> int:
    """Return how many new estimates contention left unretained."""
    # Retention is best effort: contention defers it to a later report, which
    # may reprice if the catalog changed meanwhile, instead of blocking or
    # failing this one. The first writer wins if reports race.
    if not fresh:
        return 0
    try:
        with store.connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO response_estimates(response,tier,quote) VALUES (?, ?, ?)",
                fresh,
            )
    except HiveError as error:
        if error.code != ErrorCode.BUSY:
            raise
        return len(fresh)
    return 0


def report(
    store: UsageStore, task: CodexTaskId, tier: PricingTier
) -> dict[str, object]:
    # One transaction gives counts, rates, source health, and gaps the same view.
    # Retained quotes keep the price evidence first used, so a later catalog
    # edit does not reprice them; unretained_estimates counts quotes this
    # report could not retain. No task ownership is read here.
    fresh: list[tuple[str, str, str]] = []
    with store.connect(write=False) as connection:
        cursor = connection.execute(
            "SELECT r.response, r.usage, m.model, m.conflicted, e.quote "
            "FROM responses r LEFT JOIN turn_models m ON r.task=m.task AND r.turn=m.turn AND r.host='codex' "
            "LEFT JOIN response_estimates e ON r.response=e.response AND e.tier=? "
            "WHERE r.task=?",
            (tier, task),
        )
        counts: Counter[str] = Counter()
        # A zero-amount quote identifies the rate schedule, not a response.
        groups: dict[Quote, tuple[int, int]] = {}
        examples: list[dict[str, object]] = []
        amount = 0
        observed = 0
        while True:
            fetched: object = cursor.fetchmany(256)
            batch = sequence(fetched, "cost batch")
            if not batch:
                break
            for raw in batch:
                identity, raw_usage, raw_model, conflicted, cached = row(raw, 5)
                response = ResponseId(string(identity, "response"))
                observed += 1
                reason: str | None = None
                quoted: Quote | None = None
                if raw_usage is None:
                    reason = "missing_usage"
                elif raw_model is None:
                    reason = "missing_model_context"
                elif integer(conflicted, "model conflict") != 0:
                    reason = "conflicting_model_context"
                else:
                    model = ModelId(string(raw_model, "configured model"))
                    usage = tokens(parse(string(raw_usage, "stored usage")))
                    quoted = estimate(fresh, response, model, tier, usage, cached)
                    if quoted is None:
                        reason = "unknown_model_price"
                if reason is not None:
                    counts[reason] += 1
                    if len(examples) < 20:
                        examples.append(
                            {
                                "response": response,
                                "reason": reason,
                                "configured_model": raw_model,
                            }
                        )
                    continue
                if quoted is None:
                    raise HiveError(
                        ErrorCode.INVALID_RECORD, "Missing estimate outcome"
                    )
                counts["priced"] += 1
                amount += quoted.amount
                key = replace(quoted, amount=UsdPicos(0))
                count, subtotal = groups.get(key, (0, 0))
                groups[key] = count + 1, subtotal + quoted.amount
        health = source_status(connection, task)
    unretained = retain(store, fresh)
    unpriced = observed - counts["priced"]
    return {
        "code": "ApiEquivalentCost",
        "task": task,
        "observed_responses": observed,
        "priced_responses": counts["priced"],
        "unpriced_responses": unpriced,
        "missing_usage": counts["missing_usage"],
        "missing_model_context": counts["missing_model_context"],
        "conflicting_model_context": counts["conflicting_model_context"],
        "unknown_model_price": counts["unknown_model_price"],
        "priced_subset_usd": dollars(amount) if counts["priced"] else None,
        "observed_estimate_usd": (
            dollars(amount) if observed and not unpriced else None
        ),
        "pricing_tier_assumption": tier,
        "observed_service_tier": None,
        "model_basis": "native turn context; live model overrides are not established",
        "coverage": "API-equivalent estimate, not billing. Configured model and assumed tier; thread association is not a token allocation. Excludes tool fees and regional uplifts.",
        "rate_groups": [
            {**card.value(), "usd": dollars(subtotal), "responses": count}
            for card, (count, subtotal) in groups.items()
        ],
        "unpriced_examples": examples,
        "unretained_estimates": unretained,
        **health,
    }
