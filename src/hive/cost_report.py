"""Persist exact rate evidence and report assumptions separately from observation."""

from collections import Counter
from dataclasses import replace
from datetime import datetime

from hive.claude_usage import Modifiers
from hive.cost_breakdown import Subtotal
from hive.errors import ErrorCode, HiveError
from hive.identity import (
    CodexTaskId,
    Host,
    PricingTier,
    ResponseId,
)
from hive.jsonvalue import integer, parse, sequence, string
from hive.pricing import Priced, PricedUsage, Pricing, Unpriced, dollars, pricing_value
from hive.request_pricing import price_response, request_context, retain
from hive.usage import timestamp
from hive.usage_store import UsageStore, row, source_status, stored_host


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
            "FROM responses r LEFT JOIN turn_models m ON r.task=m.task AND r.turn=m.turn AND COALESCE(r.agent,'')=m.agent AND r.host='codex' "
            "LEFT JOIN response_estimates e ON r.response=e.response AND e.tier=CASE WHEN r.host='claude' THEN r.modifier_key ELSE ? END "
            "WHERE r.task=?",
            (selected_tier, task),
        )
        counts: Counter[str] = Counter()
        # Immutable rate evidence identifies a schedule independently of usage.
        groups: dict[Pricing, tuple[int, int]] = {}
        examples: list[dict[str, object]] = []
        amount = 0
        server_fees = 0
        observed = 0
        last_priced: datetime | None = None
        observed_modifiers: Counter[Modifiers] = Counter()
        agents: dict[str | None, Subtotal] = {}
        skills: dict[str | None, Subtotal] = {}
        allocation_quotes: dict[str, PricedUsage] = {}
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
                outcome = price_response(
                    fresh,
                    response,
                    raw_usage,
                    raw_model,
                    cached,
                    request_context(request_host, modifiers, conflicted, selected_tier),
                    flags,
                )
                quoted = outcome.usage if isinstance(outcome, Priced) else None
                if request_host == Host.CLAUDE:
                    agent = None if raw_agent is None else string(raw_agent, "agent")
                    skill = None if raw_skill is None else string(raw_skill, "skill")
                    subtotal = None if quoted is None else int(quoted.amount)
                    agents[agent] = agents.get(agent, Subtotal()).add(subtotal)
                    skills[skill] = skills.get(skill, Subtotal()).add(subtotal)
                if isinstance(outcome, Unpriced):
                    reason = outcome.reason
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
                key = quoted.evidence
                count, subtotal = groups.get(key, (0, 0))
                groups[key] = count + 1, subtotal + quoted.amount
        health = source_status(connection, task)
        host_totals: dict[str, object] = {}
        event_totals: dict[str, object] = {}
        breakdowns: dict[str, object] = {}
        if host == Host.CLAUDE:
            from hive.claude_cost_state import comparison

            host_totals = comparison(connection, task, amount, last_priced)
            from hive.event_evidence import read as event_evidence
            from hive.event_report import report as event_report

            event_totals = event_report(
                event_evidence(
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
            )
            from hive.cost_breakdown import report as breakdown_report

            breakdowns = breakdown_report(
                connection, task, agents, skills, event_totals
            )
            from hive.tool_report import report as allocation_report

            breakdowns.update(allocation_report(connection, task, allocation_quotes))
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
            {**pricing_value(card), "usd": dollars(subtotal), "responses": count}
            for card, (count, subtotal) in groups.items()
        ],
        "unpriced_examples": examples,
        "unretained_estimates": unretained,
        **host_totals,
        **event_totals,
        **breakdowns,
        **health,
    }
