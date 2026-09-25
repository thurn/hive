"""Request pricing and best-effort retention, independent of report presentation."""

import json
import sqlite3
from dataclasses import dataclass

from hive.claude_usage import Modifiers
from hive.errors import ErrorCode, HiveError
from hive.identity import Host, ModelId, PricingTier, ResponseId, ThreadId
from hive.jsonvalue import integer, parse, string
from hive.price_evidence import adopt_event_rates, apply_updates, usage_updates
from hive.pricing import (
    Price,
    Priced,
    PricedUsage,
    Unpriced,
    claude_quote,
    claude_reason,
    quote,
)
from hive.usage import Tokens, tokens
from hive.usage_store import UsageStore, row


@dataclass(frozen=True)
class CodexRequest:
    tier: PricingTier
    conflicted: bool


@dataclass(frozen=True)
class ClaudeRequest:
    modifiers: Modifiers


Request = CodexRequest | ClaudeRequest


def request_context(
    host: Host, modifiers: Modifiers | None, conflicted: object, tier: PricingTier
) -> Request:
    """Validate stored host columns once before entering host-specific pricing."""
    if host == Host.CLAUDE:
        if modifiers is None:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Missing Claude modifier observation"
            )
        return ClaudeRequest(modifiers)
    if modifiers is not None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Codex request has Claude modifiers")
    return CodexRequest(
        tier, conflicted is not None and integer(conflicted, "model conflict") != 0
    )


def estimate(
    fresh: list[tuple[str, str, str]],
    response: ResponseId,
    model: ModelId,
    tier: PricingTier,
    usage: Tokens,
    cached: object,
) -> Price:
    if cached is not None:
        result = PricedUsage.read(parse(string(cached, "stored estimate")), usage)
        if result.model != model or result.tier != tier or result.host != Host.CODEX:
            raise HiveError(ErrorCode.INVALID_RECORD, "Stored price identity disagrees")
        return Priced(result)
    result = quote(model, tier, usage)
    if result is not None:
        fresh.append((response, tier, json.dumps(result.value())))
    return Priced(result) if result is not None else Unpriced("unknown_model_price")


def estimate_claude(
    fresh: list[tuple[str, str, str]],
    response: ResponseId,
    model: ModelId,
    modifiers: Modifiers,
    usage: Tokens,
    cached: object,
) -> Price:
    if cached is not None:
        result = PricedUsage.read(parse(string(cached, "stored estimate")), usage)
        if (
            result.host != Host.CLAUDE
            or result.model != model
            or result.modifiers != modifiers
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Stored Claude price identity disagrees"
            )
        return Priced(result)
    result = claude_quote(model, modifiers, usage)
    if result is not None:
        fresh.append((response, modifiers.key, json.dumps(result.value())))
    return (
        Priced(result)
        if result is not None
        else Unpriced(claude_reason(model, modifiers) or "unknown_modifier")
    )


def price_response(
    fresh: list[tuple[str, str, str]],
    response: ResponseId,
    raw_usage: object,
    raw_model: object,
    cached: object,
    request: Request,
    flags: tuple[str, ...],
) -> Price:
    if "unjoinable" in flags:
        return Unpriced("unjoinable_event")
    if raw_usage is None:
        return Unpriced("missing_usage")
    if raw_model is None:
        return Unpriced("missing_model_context")
    if isinstance(request, CodexRequest) and request.conflicted:
        return Unpriced("conflicting_model_context")
    if "unsupported_iteration" in flags:
        return Unpriced("unsupported_iteration")
    model = ModelId(string(raw_model, "configured model"))
    usage = tokens(parse(string(raw_usage, "stored usage")))
    if isinstance(request, ClaudeRequest):
        return estimate_claude(fresh, response, model, request.modifiers, usage, cached)
    return estimate(fresh, response, model, request.tier, usage, cached)


def retain(
    store: UsageStore,
    fresh: list[tuple[str, str, str]],
) -> int:
    """Return how many new estimates contention left unretained."""
    # Retention is best effort: contention defers it to a later report, which
    # may reprice if the catalog changed meanwhile, instead of blocking or
    # failing this one. The first writer wins if reports race.
    if not fresh:
        return 0
    try:
        with store.connect() as connection:
            persist(connection, fresh)
    except HiveError as error:
        if error.code != ErrorCode.BUSY:
            raise
        return len(fresh)
    return 0


def persist(
    connection: sqlite3.Connection,
    fresh: list[tuple[str, str, str]],
) -> None:
    """Retain first rate evidence and refresh exact projections in the writer's transaction."""
    connection.executemany(
        "INSERT OR IGNORE INTO response_estimates(response,tier,quote) VALUES (?, ?, ?)",
        fresh,
    )
    # Collection may have completed a stream since the read snapshot.
    # Keep the first writer's rates and use the latest committed tokens.
    for response, _, _ in fresh:
        current: object = connection.execute(
            "SELECT host,usage,model,modifiers FROM responses WHERE response=?",
            (response,),
        ).fetchone()
        if current is not None:
            host, usage, model, modifiers = row(current, 4)
            if host == Host.CLAUDE and usage is not None:
                adopt_event_rates(
                    connection,
                    ResponseId(response),
                    string(model, "model"),
                    Modifiers.read(parse(string(modifiers, "modifiers"))),
                    tokens(parse(string(usage, "usage"))),
                )
                apply_updates(
                    connection,
                    usage_updates(
                        connection,
                        ResponseId(response),
                        tokens(parse(string(usage, "usage"))),
                    ),
                )
    from hive.tool_allocation_store import retain as retain_allocations

    tasks: dict[ThreadId, set[str]] = {}
    for response, _, _ in fresh:
        owner: object = connection.execute(
            "SELECT task FROM responses WHERE response=? AND host='claude'",
            (response,),
        ).fetchone()
        if owner is not None:
            tasks.setdefault(
                ThreadId(string(row(owner, 1)[0], "allocation task")), set()
            ).add(response)
    for task in sorted(tasks):
        retain_allocations(connection, task, tuple(sorted(tasks[task])))
