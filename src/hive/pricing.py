"""Exact API-equivalent arithmetic; prices are observations, never billing truth."""

from dataclasses import dataclass, replace

from hive.claude_usage import Modifiers
from hive.errors import ErrorCode, HiveError
from hive.identity import Host, ModelId, UsdPicos
from hive.identity import PricingTier as Tier
from hive.jsonvalue import integer, record, string
from hive.usage import Tokens


@dataclass(frozen=True)
class Rates:
    # A millionth of a dollar per million tokens is a picodollar per token.
    input: int
    cached: int
    cache_write: int
    output: int
    cache_write_1h: int = 0

    def value(self) -> dict[str, object]:
        return {
            "input": self.input,
            "cached": self.cached,
            "cache_write": self.cache_write,
            "output": self.output,
            "cache_write_1h": self.cache_write_1h,
        }


@dataclass(frozen=True)
class Quote:
    model: ModelId
    tier: Tier | None
    long_context: bool
    rates: Rates
    price_observed: str
    source: str
    amount: UsdPicos
    host: Host = Host.CODEX
    modifiers: Modifiers | None = None
    web_search_picos: int = 0

    @property
    def modifier_key(self) -> str:
        if self.host == Host.CLAUDE and self.modifiers is not None:
            return self.modifiers.key
        if self.host == Host.CODEX and self.tier is not None:
            return self.tier.value
        raise HiveError(ErrorCode.INVALID_RECORD, "Missing quote modifier identity")

    def components(self, usage: Tokens) -> dict[str, int]:
        return {
            "input": (
                usage.input
                - usage.cached_input
                - usage.cache_write_input
                - usage.cache_write_1h_input
            )
            * self.rates.input,
            "cache_read": usage.cached_input * self.rates.cached,
            "cache_write_5m": usage.cache_write_input * self.rates.cache_write,
            "cache_write_1h": usage.cache_write_1h_input * self.rates.cache_write_1h,
            "output": usage.output * self.rates.output,
            "server_tools": (
                0 if self.modifiers is None else self.modifiers.web_searches
            )
            * self.web_search_picos,
        }

    def with_usage(self, usage: Tokens) -> "Quote":
        return replace(self, amount=UsdPicos(sum(self.components(usage).values())))

    def value(self) -> dict[str, object]:
        return {
            "host": self.host,
            "modifier_key": self.modifier_key,
            "modifiers": None if self.modifiers is None else self.modifiers.value(),
            "server_tool_fees": {"web_search_picos": self.web_search_picos},
            "model": self.model,
            "tier": self.tier,
            "long_context": self.long_context,
            "rates_picos_per_token": self.rates.value(),
            "price_observed": self.price_observed,
            "source": self.source,
            "usd": dollars(self.amount),
        }

    @classmethod
    def read(cls, value: object) -> "Quote":
        data = record(value, "stored price estimate")
        rates = record(data.get("rates_picos_per_token"), "stored rates")
        amount = string(data.get("usd"), "stored USD estimate")
        whole, separator, fraction = amount.partition(".")
        if (
            not separator
            or not whole.isascii()
            or not whole.isdigit()
            or len(fraction) != 12
            or not fraction.isascii()
            or not fraction.isdigit()
        ):
            raise HiveError(ErrorCode.INVALID_RECORD, "Invalid stored USD amount")
        band = data.get("long_context")
        if not isinstance(band, bool):
            raise HiveError(ErrorCode.INVALID_RECORD, "Unknown stored context band")
        try:
            host = Host(string(data.get("host", "codex"), "stored host"))
            tier = (
                Tier(string(data.get("tier"), "stored tier"))
                if host == Host.CODEX
                else None
            )
        except ValueError as error:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Unknown stored host or tier"
            ) from error
        modifiers = (
            Modifiers.read(data.get("modifiers")) if host == Host.CLAUDE else None
        )
        key = string(data.get("modifier_key", tier), "stored modifier key")
        if (host == Host.CODEX and key != tier) or (
            modifiers is not None
            and (
                key != modifiers.key
                or modifiers.speed not in {"standard", "fast"}
                or modifiers.service_tier != "standard"
                or modifiers.inference_geo not in {"global", "not_available", "us"}
            )
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Invalid stored modifier identity"
            )
        fees = record(data.get("server_tool_fees", {}), "stored server fees")
        return cls(
            ModelId(string(data.get("model"), "stored model")),
            tier,
            band,
            Rates(
                *(
                    integer(rates.get(k), k)
                    for k in ("input", "cached", "cache_write", "output")
                ),
                cache_write_1h=integer(
                    rates.get("cache_write_1h", 0), "1h cache write rate"
                ),
            ),
            string(data.get("price_observed"), "price observation"),
            string(data.get("source"), "price source"),
            UsdPicos(int(whole) * 10**12 + int(fraction)),
            host,
            modifiers,
            integer(fees.get("web_search_picos", 0), "web search fee"),
        )


def dollars(value: int) -> str:
    whole, fraction = divmod(value, 10**12)
    return f"{whole}.{fraction:012d}"


def quote(model: ModelId, tier: Tier, usage: Tokens) -> Quote | None:
    # Exact model IDs only. Unknown aliases and future models stay unpriced.
    # Official model pages specify >272K input, including cached input, for
    # full-request 2x input/cache and 1.5x output pricing. Rates checked 2026-09-23.
    base = {
        "gpt-6-astra": Rates(10_000_000, 1_000_000, 12_500_000, 50_000_000),
        "gpt-6-sol": Rates(2_000_000, 200_000, 2_500_000, 10_000_000),
        "gpt-6-luna": Rates(100_000, 10_000, 125_000, 500_000),
    }.get(model)
    if base is None:
        return None
    long_context = usage.input > 272_000
    numerator = 2 if tier == Tier.FAST else 1
    denominator = 2 if tier in {Tier.BATCH, Tier.FLEX} else 1
    input_factor = 2 if long_context else 1
    rates = Rates(
        base.input * input_factor * numerator // denominator,
        base.cached * input_factor * numerator // denominator,
        base.cache_write * input_factor * numerator // denominator,
        base.output * (3 if long_context else 2) * numerator // (2 * denominator),
    )
    amount = (
        (usage.input - usage.cached_input - usage.cache_write_input) * rates.input
        + usage.cached_input * rates.cached
        + usage.cache_write_input * rates.cache_write
        + usage.output * rates.output
    )
    return Quote(
        model,
        tier,
        long_context,
        rates,
        "2026-09-23",
        "https://developers.openai.com/api/docs/pricing",
        UsdPicos(amount),
    )


# Explicit rates (input, cache read, 5m write, output, 1h write), in
# picodollars/token. Observed 2026-09-24 at the official pricing URL below.
# A tuple keeps the checked-in rate evidence immutable.
CLAUDE_RATES: tuple[tuple[str, Rates], ...] = (
    (
        "claude-fable-5-1",
        Rates(10_000_000, 250_000, 12_500_000, 50_000_000, 20_000_000),
    ),
    (
        "claude-fable-5",
        Rates(10_000_000, 1_000_000, 12_500_000, 50_000_000, 20_000_000),
    ),
    ("claude-opus-5-5", Rates(4_000_000, 200_000, 5_000_000, 20_000_000, 8_000_000)),
    ("claude-opus-5", Rates(5_000_000, 500_000, 6_250_000, 25_000_000, 10_000_000)),
    ("claude-opus-4-8", Rates(5_000_000, 500_000, 6_250_000, 25_000_000, 10_000_000)),
    ("claude-opus-4-7", Rates(5_000_000, 500_000, 6_250_000, 25_000_000, 10_000_000)),
    ("claude-opus-4-6", Rates(5_000_000, 500_000, 6_250_000, 25_000_000, 10_000_000)),
    ("claude-sonnet-5", Rates(2_000_000, 200_000, 2_500_000, 10_000_000, 4_000_000)),
    ("claude-sonnet-4-6", Rates(3_000_000, 300_000, 3_750_000, 15_000_000, 6_000_000)),
    ("claude-haiku-4-5", Rates(1_000_000, 100_000, 1_250_000, 5_000_000, 2_000_000)),
)


def claude_reason(model: ModelId, modifiers: Modifiers) -> str | None:
    if not any(name == model for name, _ in CLAUDE_RATES):
        return "unknown_model_price"
    if modifiers.service_tier != "standard":
        return "unknown_service_tier"
    if (
        modifiers.speed not in {"standard", "fast"}
        or (
            modifiers.speed == "fast"
            and model not in {"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"}
        )
        or modifiers.inference_geo not in {"global", "not_available", "us"}
        or (modifiers.inference_geo == "us" and model == "claude-haiku-4-5")
    ):
        return "unknown_modifier"
    return None


def claude_quote(model: ModelId, modifiers: Modifiers, usage: Tokens) -> Quote | None:
    if claude_reason(model, modifiers) is not None:
        return None
    base = next(rates for name, rates in CLAUDE_RATES if name == model)
    numerator = (2 if modifiers.speed == "fast" else 1) * (
        11 if modifiers.inference_geo == "us" else 10
    )
    rates = Rates(
        *(
            value * numerator // 10
            for value in (
                base.input,
                base.cached,
                base.cache_write,
                base.output,
                base.cache_write_1h,
            )
        )
    )
    evidence = Quote(
        model,
        None,
        False,
        rates,
        "2026-09-24",
        "https://platform.claude.com/docs/en/about-claude/pricing",
        UsdPicos(0),
        Host.CLAUDE,
        modifiers,
        10_000_000_000,
    )
    return evidence.with_usage(usage)
