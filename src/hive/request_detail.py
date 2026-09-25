"""Stable keyset pages of request costs, with best-effort rate retention."""

import base64
import binascii
import json

from hive.claude_usage import Modifiers
from hive.errors import ErrorCode, HiveError
from hive.identity import Host, PricingTier, ResponseId, ThreadId
from hive.jsonvalue import parse, record, sequence, string
from hive.pricing import Priced, dollars
from hive.request_pricing import price_response, request_context, retain
from hive.usage_store import UsageStore, row, stored_host

PAGE_SIZE = 500


def cursor_after(cursor: str | None, task: ThreadId, tier: PricingTier) -> str:
    if cursor is None:
        return ""
    try:
        if len(cursor) > 8192:
            raise ValueError("Oversized request cursor")
        value = record(
            parse(
                base64.b64decode(cursor, altchars=b"-_", validate=True).decode("utf-8")
            )
        )
        if (
            value.get("task") != task
            or value.get("tier") != tier
            or value.get("version") != 1
        ):
            raise ValueError("Request cursor belongs to another query")
        return string(value.get("after"), "request cursor response")
    except (HiveError, ValueError, UnicodeError, binascii.Error) as error:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid request cursor") from error


def encode_cursor(task: ThreadId, tier: PricingTier, after: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"version": 1, "task": task, "tier": tier, "after": after}).encode()
    ).decode("ascii")


def report(
    store: UsageStore,
    task: ThreadId,
    tier: PricingTier | None = None,
    *,
    cursor: str | None = None,
    host_hint: Host | None = None,
) -> dict[str, object]:
    selected_tier = tier or PricingTier.STANDARD
    after = cursor_after(cursor, task, selected_tier)
    fresh: list[tuple[str, str, str]] = []
    output: list[dict[str, object]] = []
    new_responses: set[str] = set()
    with store.connect(write=False) as connection:
        host = stored_host(connection, task) or host_hint
        if host == Host.CLAUDE and tier is not None:
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Claude observes modifiers per request; --tier is not supported",
            )
        result = connection.execute(
            "SELECT * FROM request_detail WHERE thread=? AND (tier IS NULL OR tier=?) "
            "AND response>? ORDER BY response LIMIT ?",
            (task, selected_tier, after, PAGE_SIZE + 1),
        )
        # sqlite3's dynamic result remains outside application state until its
        # column names and values have crossed these explicit validators.
        description: object = result.description
        if not isinstance(description, tuple):
            raise HiveError(ErrorCode.INVALID_RECORD, "Missing request view columns")
        names = tuple(
            string(row(column, 7)[0], "request column") for column in description
        )
        fetched: object = result.fetchall()
        values = sequence(fetched, "request page")
        more = len(values) > PAGE_SIZE
        for raw in values[:PAGE_SIZE]:
            detail = dict(zip(names, row(raw, len(names)), strict=True))
            response = ResponseId(string(detail["response"], "response"))
            request_host = Host(string(detail["host"], "request host"))
            if request_host == Host.CLAUDE and tier is not None:
                raise HiveError(
                    ErrorCode.INVALID_INPUT,
                    "Claude observes modifiers per request; --tier is not supported",
                )
            flags = tuple(
                string(flag, "request flag")
                for flag in sequence(parse(string(detail["flags"], "flags")), "flags")
            )
            modifiers = (
                Modifiers.read(parse(string(detail["_modifiers"], "modifiers")))
                if request_host == Host.CLAUDE
                else None
            )
            outcome = price_response(
                fresh,
                response,
                detail["_usage"],
                detail["model"],
                detail["_quote"],
                request_context(
                    request_host, modifiers, detail["_conflicted"], selected_tier
                ),
                flags,
            )
            quoted = outcome.usage if isinstance(outcome, Priced) else None
            detail["flags"] = list(flags)
            detail["unpriced_reason"] = (
                None if isinstance(outcome, Priced) else outcome.reason
            )
            if quoted is not None:
                detail.update(
                    {
                        "usd_" + key: dollars(amount)
                        for key, amount in quoted.components().items()
                    }
                )
                detail["usd"] = dollars(quoted.amount)
                if detail["_quote"] is None:
                    new_responses.add(response)
            output.append(
                {key: value for key, value in detail.items() if not key.startswith("_")}
            )
    unretained = retain(store, fresh)
    if unretained:
        for detail in output:
            if detail["response"] in new_responses:
                detail["flags"] = [*sequence(detail["flags"], "flags"), "unretained"]
    return {
        "code": "ApiEquivalentRequestCosts",
        "task": task,
        "host": host,
        "requests": output,
        "page_size": PAGE_SIZE,
        "next_cursor": (
            encode_cursor(
                task, selected_tier, string(output[-1]["response"], "response")
            )
            if more
            else None
        ),
        "unretained_estimates": unretained,
        "pricing_tier_assumption": None if host == Host.CLAUDE else selected_tier,
    }
