"""Derive event-only cache TTL and geo from host micro-dollar evidence."""

from dataclasses import dataclass

from hive.claude_events import Request
from hive.claude_usage import Modifiers
from hive.pricing import claude_quote
from hive.usage import Tokens


@dataclass(frozen=True)
class Derived:
    usage: Tokens
    modifiers: Modifiers
    flags: tuple[str, ...]


def derive(event: Request) -> Derived:
    speed = "standard" if event.speed == "normal" else event.speed
    base_flags = ("tier_assumed", "web_search_unknown", "thinking_unmeasured")
    base = Tokens(
        event.input + event.cached + event.writes,
        event.cached,
        event.writes,
        event.output,
        0,
    )
    for geo in ("global", "us"):
        modifiers = Modifiers(speed, "standard", geo, 0)
        quoted = claude_quote(event.model, modifiers, base)
        if quoted is None:
            continue
        step = quoted.rates.cache_write_1h - quoted.rates.cache_write
        difference = event.micros * 1_000_000 - quoted.amount
        # Event amounts are rounded to micro-dollars. Search the nearest
        # integral split and retain the first geography satisfying that bound.
        if step <= 0:
            continue
        nearest = max(0, min(event.writes, (difference + step // 2) // step))
        if abs(quoted.amount + nearest * step - event.micros * 1_000_000) <= 1_000_000:
            usage = Tokens(
                base.input,
                base.cached_input,
                event.writes - nearest,
                base.output,
                0,
                nearest,
            )
            flags = (
                *base_flags,
                "cache_ttl_derived",
                *(("geo_derived",) if geo == "us" else ()),
            )
            return Derived(usage, modifiers, flags)
    return Derived(
        Tokens(base.input, base.cached_input, 0, base.output, 0, event.writes),
        Modifiers(speed, "standard", "not_available", 0),
        (*base_flags, "cache_ttl_assumed", "geo_assumed"),
    )
