"""Integer positional pricing with explicitly estimated splits inside segments."""

from dataclasses import dataclass

from hive.pricing import Quote
from hive.tool_parts import Part
from hive.usage import Tokens


@dataclass(frozen=True)
class Segment:
    size: int
    parts: tuple[Part, ...]
    thinking: int = 0


@dataclass(frozen=True)
class Charge:
    bucket: str
    tool: str | None
    ref: str | None
    phase: str
    amount: int
    method: str
    component: str
    tokens: int


def split(total: int, weights: tuple[int, ...]) -> tuple[int, ...]:
    """Largest remainder; equal remainders retain source part order."""
    if not weights:
        return ()
    denominator = sum(weights)
    if denominator == 0:
        weights = (1,) * len(weights)
        denominator = len(weights)
    divisions = tuple(divmod(total * weight, denominator) for weight in weights)
    order = sorted(range(len(weights)), key=lambda i: (-divisions[i][1], i))
    remainder = total - sum(value[0] for value in divisions)
    winners = set(order[:remainder])
    return tuple(value[0] + int(i in winners) for i, value in enumerate(divisions))


def label(part: Part, names: dict[str, str]) -> tuple[str, str | None]:
    if part.kind in {"tool_use", "tool_result"}:
        name = part.name or names.get(part.ref or "", "unknown")
        return "tool:" + name, name
    return {
        "text": "assistant_text",
        "user": "user_input",
        "thinking": "thinking",
        "reminder": "reminders_and_attachments",
        "attachment": "reminders_and_attachments",
        "oversized": "reminders_and_attachments",
    }.get(part.kind, part.kind), None


def weighted(segment: Segment) -> tuple[tuple[Part, ...], tuple[int, ...], str]:
    nonthinking = tuple(part for part in segment.parts if part.kind != "thinking")
    has_thinking = any(part.kind == "thinking" for part in segment.parts)
    thinking = min(segment.size, segment.thinking) if has_thinking else 0
    if not nonthinking and thinking < segment.size:
        nonthinking = (Part("rewritten_context", None, None, 1),)
    weights: tuple[int, ...] = tuple(part.size for part in nonthinking)
    values: tuple[Part, ...]
    if weights and not sum(weights):
        weights = (1,) * len(weights)
    if thinking:
        values = (Part("thinking", None, None, 0),) + nonthinking
        denominator = sum(weights) or 1
        weights = (thinking * denominator,) + tuple(
            weight * (segment.size - thinking) for weight in weights
        )
    else:
        values = nonthinking
    method = (
        "bytes_with_oversized"
        if any(part.kind == "oversized" for part in values)
        else "exact" if len(values) == 1 and not has_thinking else "bytes"
    )
    return values, weights, method


def allocate(
    usage: Tokens,
    quote: Quote,
    segments: tuple[Segment, ...],
    blocks: tuple[Part, ...],
    names: dict[str, str],
    *,
    thinking_measured: bool,
) -> tuple[Charge, ...]:
    charges: list[Charge] = []
    bands = (
        (usage.cached_input, quote.rates.cached, "cache_read"),
        (usage.cache_write_1h_input, quote.rates.cache_write_1h, "cache_write_1h"),
        (usage.cache_write_input, quote.rates.cache_write, "cache_write_5m"),
        (
            usage.input
            - usage.cached_input
            - usage.cache_write_1h_input
            - usage.cache_write_input,
            quote.rates.input,
            "input",
        ),
    )
    segment_start = 0
    for segment in segments:
        parts, weights, method = weighted(segment)
        band_start = 0
        for count, rate, component in bands:
            overlap = max(
                0,
                min(segment_start + segment.size, band_start + count)
                - max(segment_start, band_start),
            )
            for part, tokens in zip(parts, split(overlap, weights), strict=True):
                bucket, tool = label(part, names)
                if tokens:
                    charges.append(
                        Charge(
                            bucket,
                            tool,
                            part.ref,
                            "carrying",
                            tokens * rate,
                            method,
                            component,
                            tokens,
                        )
                    )
            band_start += count
        segment_start += segment.size
    thinking = usage.reasoning_output if thinking_measured else 0
    if thinking:
        charges.append(
            Charge(
                "thinking",
                None,
                None,
                "invocation",
                thinking * quote.rates.output,
                "exact",
                "output",
                thinking,
            )
        )
    output = tuple(part for part in blocks if part.kind != "thinking")
    if not output:
        output = (Part("text", None, None, 1),)
    for part, count in zip(
        output,
        split(usage.output - thinking, tuple(part.size for part in output)),
        strict=True,
    ):
        bucket, tool = label(part, names)
        charges.append(
            Charge(
                bucket,
                tool,
                part.ref,
                "invocation",
                count * quote.rates.output,
                "exact" if len(output) == 1 else "bytes",
                "output",
                count,
            )
        )
    fees = (
        0 if quote.modifiers is None else quote.modifiers.web_searches
    ) * quote.web_search_picos
    if fees:
        charges.append(
            Charge(
                "server_tool_fees", None, None, "fee", fees, "exact", "server_tools", 0
            )
        )
    return tuple(charges)
