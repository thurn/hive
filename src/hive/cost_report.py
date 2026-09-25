"""Persist exact rate evidence and report assumptions separately from observation."""

import json
from collections import Counter
from dataclasses import replace
from datetime import datetime

from hive.claude_usage import Modifiers
from hive.cost_breakdown import Subtotal
from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, Host, ModelId, PricingTier, ResponseId, UsdPicos
from hive.jsonvalue import integer, parse, sequence, string
from hive.price_evidence import adopt_event_rates, apply_updates, usage_updates
from hive.pricing import Quote, claude_quote, claude_reason, dollars, quote
from hive.usage import Tokens, timestamp, tokens
from hive.usage_store import UsageStore, row, source_status, stored_host


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
        if result.model != model or result.tier != tier or result.host != Host.CODEX:
            raise HiveError(ErrorCode.INVALID_RECORD, "Stored price identity disagrees")
        return result
    result = quote(model, tier, usage)
    if result is not None:
        fresh.append((response, tier, json.dumps(result.value(usage))))
    return result


def estimate_claude(
    fresh: list[tuple[str, str, str]],
    response: ResponseId,
    model: ModelId,
    modifiers: Modifiers,
    usage: Tokens,
    cached: object,
) -> tuple[Quote | None, str | None]:
    if cached is not None:
        result = Quote.read(parse(string(cached, "stored estimate")))
        if (
            result.host != Host.CLAUDE
            or result.model != model
            or result.modifiers != modifiers
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Stored Claude price identity disagrees"
            )
        return result.with_usage(usage), None
    result = claude_quote(model, modifiers, usage)
    if result is not None:
        fresh.append((response, modifiers.key, json.dumps(result.value(usage))))
    return result, claude_reason(model, modifiers)


def price_response(
    fresh: list[tuple[str, str, str]],
    response: ResponseId,
    raw_usage: object,
    raw_model: object,
    conflicted: object,
    cached: object,
    host: Host,
    modifiers: Modifiers | None,
    flags: tuple[str, ...],
    tier: PricingTier,
) -> tuple[Quote | None, str | None]:
    if "unjoinable" in flags:
        return None, "unjoinable_event"
    if raw_usage is None:
        return None, "missing_usage"
    if raw_model is None:
        return None, "missing_model_context"
    if host == Host.CODEX and integer(conflicted, "model conflict") != 0:
        return None, "conflicting_model_context"
    if "unsupported_iteration" in flags:
        return None, "unsupported_iteration"
    model = ModelId(string(raw_model, "configured model"))
    usage = tokens(parse(string(raw_usage, "stored usage")))
    if modifiers is not None:
        return estimate_claude(fresh, response, model, modifiers, usage, cached)
    quoted = estimate(fresh, response, model, tier, usage, cached)
    return quoted, "unknown_model_price" if quoted is None else None


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
    except HiveError as error:
        if error.code != ErrorCode.BUSY:
            raise
        return len(fresh)
    return 0


def report(
    store: UsageStore,
    task: CodexTaskId,
    tier: PricingTier | None = None,
    *,
    host_hint: Host | None = None,
) -> dict[str, object]:
    # One transaction gives counts, rates, source health, and gaps the same view.
    # Retained quotes keep the price evidence first used, so a later catalog
    # edit does not reprice them; unretained_estimates counts quotes this
    # report could not retain. No task ownership is read here.
    fresh: list[tuple[str, str, str]] = []
    selected_tier = tier or PricingTier.STANDARD
    with store.connect(write=False) as connection:
        host = stored_host(connection, task) or host_hint
        if host == Host.CLAUDE and tier is not None:
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Claude observes modifiers per request; --tier is not supported",
            )
        cursor = connection.execute(
            "SELECT r.response, r.usage, CASE WHEN r.host='claude' THEN r.model ELSE m.model END, m.conflicted, e.quote, r.host, r.modifiers, r.flags, r.complete, COALESCE(r.last_observed,r.observed),r.agent,r.skill "
            "FROM responses r LEFT JOIN turn_models m ON r.task=m.task AND r.turn=m.turn AND r.host='codex' "
            "LEFT JOIN response_estimates e ON r.response=e.response AND e.tier=CASE WHEN r.host='claude' THEN r.modifier_key ELSE ? END "
            "WHERE r.task=?",
            (selected_tier, task),
        )
        counts: Counter[str] = Counter()
        # A zero-amount quote identifies the rate schedule, not a response.
        groups: dict[Quote, tuple[int, int]] = {}
        examples: list[dict[str, object]] = []
        amount = 0
        server_fees = 0
        observed = 0
        last_priced: datetime | None = None
        observed_modifiers: Counter[Modifiers] = Counter()
        agents: dict[str | None, Subtotal] = {}
        skills: dict[str | None, Subtotal] = {}
        allocation_quotes: dict[str, Quote] = {}
        while True:
            fetched: object = cursor.fetchmany(256)
            batch = sequence(fetched, "cost batch")
            if not batch:
                break
            for raw in batch:
                (
                    identity,
                    raw_usage,
                    raw_model,
                    conflicted,
                    cached,
                    raw_host,
                    raw_modifiers,
                    raw_flags,
                    complete,
                    observed_at,
                    raw_agent,
                    raw_skill,
                ) = row(raw, 12)
                request_host = Host(string(raw_host, "request host"))
                flags = tuple(
                    string(value, "usage flag")
                    for value in sequence(
                        parse(string(raw_flags, "usage flags")), "usage flags"
                    )
                )
                modifiers = (
                    Modifiers.read(parse(string(raw_modifiers, "request modifiers")))
                    if request_host == Host.CLAUDE
                    else None
                )
                if modifiers is not None:
                    observed_modifiers[replace(modifiers, web_searches=0)] += 1
                    counts["possibly_partial_output"] += int(
                        not integer(complete, "complete")
                    )
                    counts["ttl_assumed"] += int("ttl_assumed" in flags)
                    if tier is not None:
                        raise HiveError(
                            ErrorCode.INVALID_INPUT,
                            "Claude observes modifiers per request; --tier is not supported",
                        )
                response = ResponseId(string(identity, "response"))
                observed += 1
                quoted, reason = price_response(
                    fresh,
                    response,
                    raw_usage,
                    raw_model,
                    conflicted,
                    cached,
                    request_host,
                    modifiers,
                    flags,
                    selected_tier,
                )
                if request_host == Host.CLAUDE:
                    agent = None if raw_agent is None else string(raw_agent, "agent")
                    skill = None if raw_skill is None else string(raw_skill, "skill")
                    subtotal = None if quoted is None else int(quoted.amount)
                    agents[agent] = agents.get(agent, Subtotal()).add(subtotal)
                    skills[skill] = skills.get(skill, Subtotal()).add(subtotal)
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
                observed_time = timestamp(observed_at)
                if last_priced is None or observed_time > last_priced:
                    last_priced = observed_time
                if request_host == Host.CLAUDE:
                    allocation_quotes[response] = quoted
                counts["priced"] += 1
                amount += quoted.amount
                server_fees += (
                    0 if quoted.modifiers is None else quoted.modifiers.web_searches
                ) * quoted.web_search_picos
                key = replace(quoted, amount=UsdPicos(0))
                count, subtotal = groups.get(key, (0, 0))
                groups[key] = count + 1, subtotal + quoted.amount
        health = source_status(connection, task)
        host_totals: dict[str, object] = {}
        event_totals: dict[str, object] = {}
        breakdowns: dict[str, object] = {}
        if host == Host.CLAUDE:
            from hive.claude_cost_state import comparison

            host_totals = comparison(connection, task, amount, last_priced)
            from hive.event_report import report as event_report

            event_totals = event_report(
                connection,
                store.path.parent,
                task,
                fresh,
                observed=observed,
                priced=counts["priced"],
                amount=amount,
                partial=counts["possibly_partial_output"],
                health=health,
                host_totals=host_totals,
            )
            from hive.cost_breakdown import report as breakdown_report

            breakdowns = breakdown_report(
                connection, task, agents, skills, event_totals
            )
            from hive.tool_report import report as allocation_report

            breakdowns.update(
                allocation_report(connection, task, allocation_quotes).details
            )
    unretained = retain(store, fresh)
    unpriced = observed - counts["priced"]
    return {
        "code": "ApiEquivalentCost",
        "task": task,
        "host": host,
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
        "possibly_partial_output": counts["possibly_partial_output"],
        "ttl_assumed": counts["ttl_assumed"],
        "unknown_modifier": counts["unknown_modifier"],
        "unknown_service_tier": counts["unknown_service_tier"],
        "unsupported_iteration": counts["unsupported_iteration"],
        "server_tool_fees_usd": dollars(server_fees),
        "observed_modifiers": [
            {
                "speed": modifiers.speed,
                "service_tier": modifiers.service_tier,
                "inference_geo": modifiers.inference_geo,
                "responses": count,
            }
            for modifiers, count in observed_modifiers.items()
        ],
        "pricing_tier_assumption": None if host == Host.CLAUDE else selected_tier,
        "observed_service_tier": None,
        "model_basis": (
            "observed per request"
            if host == Host.CLAUDE
            else "native turn context; live model overrides are not established"
        ),
        "coverage": (
            "API-equivalent estimate from recorded requests, not billing; Claude Code makes requests its transcript omits, so this is a lower bound. A host total with has_unknown_model_cost is itself incomplete."
            if host == Host.CLAUDE
            else "API-equivalent estimate, not billing. Configured model and assumed tier; thread association is not a token allocation. Excludes tool fees and regional uplifts."
        ),
        "rate_groups": [
            {**card.value(), "usd": dollars(subtotal), "responses": count}
            for card, (count, subtotal) in groups.items()
        ],
        "unpriced_examples": examples,
        "unretained_estimates": unretained,
        **host_totals,
        **event_totals,
        **breakdowns,
        **health,
    }
