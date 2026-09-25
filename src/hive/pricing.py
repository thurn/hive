"""Exact API-equivalent arithmetic; prices are observations, never billing truth."""

from dataclasses import dataclass
from typing import Literal

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
class PriceableModifiers:
    """Validated Claude rate identity; raw unknown observations cannot enter it."""

    speed: Literal["standard", "fast"]
    inference_geo: Literal["global", "not_available", "us"]
    web_searches: int

    @classmethod
    def read(cls, raw: Modifiers) -> "PriceableModifiers":
        if (
            raw.service_tier != "standard"
            or raw.speed not in ("standard", "fast")
            or raw.inference_geo not in ("global", "not_available", "us")
        ):
            raise HiveError(ErrorCode.INVALID_RECORD, "Unpriceable Claude modifiers")
        return cls(raw.speed, raw.inference_geo, raw.web_searches)

    @property
    def observed(self) -> Modifiers:
        return Modifiers(self.speed, "standard", self.inference_geo, self.web_searches)


@dataclass(frozen=True)
class RateEvidence:
    """An observed rate card, independent of any request's materialized total."""

    model: ModelId
    long_context: bool
    rates: Rates
    price_observed: str
    source: str


@dataclass(frozen=True)
class CodexPricing:
    card: RateEvidence
    tier: Tier

    @property
    def host(self) -> Host:
        return Host.CODEX


@dataclass(frozen=True)
class ClaudePricing:
    card: RateEvidence
    modifiers: PriceableModifiers
    web_search_picos: int

    @property
    def host(self) -> Host:
        return Host.CLAUDE


Pricing = CodexPricing | ClaudePricing


def pricing_value(evidence: Pricing) -> dict[str, object]:
    card = evidence.card
    modifiers = (
        evidence.modifiers.observed if isinstance(evidence, ClaudePricing) else None
    )
    tier = evidence.tier if isinstance(evidence, CodexPricing) else None
    return {
        "host": evidence.host,
        "modifier_key": modifiers.key if modifiers is not None else tier,
        "modifiers": None if modifiers is None else modifiers.value(),
        "server_tool_fees": {
            "web_search_picos": (
                evidence.web_search_picos if isinstance(evidence, ClaudePricing) else 0
            )
        },
        "model": card.model,
        "tier": tier,
        "long_context": card.long_context,
        "rates_picos_per_token": card.rates.value(),
        "price_observed": card.price_observed,
        "source": card.source,
    }


def read_pricing(value: object) -> Pricing:
    """Decode retained evidence; persisted totals are query projections, not rates."""
    data = record(value, "stored price estimate")
    rates = record(data.get("rates_picos_per_token"), "stored rates")
    band = data.get("long_context")
    if not isinstance(band, bool):
        raise HiveError(ErrorCode.INVALID_RECORD, "Unknown stored context band")
    card = RateEvidence(
        ModelId(string(data.get("model"), "stored model")),
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
    )
    try:
        host = Host(string(data.get("host", "codex"), "stored host"))
        if host == Host.CODEX:
            tier = Tier(string(data.get("tier"), "stored tier"))
            if data.get("modifier_key", tier) != tier:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Invalid stored tier identity"
                )
            return CodexPricing(card, tier)
    except ValueError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Unknown stored host or tier"
        ) from error
    modifiers = PriceableModifiers.read(Modifiers.read(data.get("modifiers")))
    if (
        data.get("modifier_key") != modifiers.observed.key
        or data.get("tier") is not None
    ):
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid stored modifier identity")
    fees = record(data.get("server_tool_fees", {}), "stored server fees")
    return ClaudePricing(
        card, modifiers, integer(fees.get("web_search_picos", 0), "web search fee")
    )


@dataclass(frozen=True)
class PricedUsage:
    """Bind validated usage once; total, components and allocation share this value."""

    evidence: Pricing
    usage: Tokens

    @property
    def model(self) -> ModelId:
        return self.evidence.card.model

    @property
    def host(self) -> Host:
        return self.evidence.host

    @property
    def tier(self) -> Tier | None:
        return self.evidence.tier if isinstance(self.evidence, CodexPricing) else None

    @property
    def modifiers(self) -> Modifiers | None:
        return (
            self.evidence.modifiers.observed
            if isinstance(self.evidence, ClaudePricing)
            else None
        )

    @property
    def modifier_key(self) -> str:
        evidence = self.evidence
        if isinstance(evidence, ClaudePricing):
            return evidence.modifiers.observed.key
        return evidence.tier.value

    @property
    def rates(self) -> Rates:
        return self.evidence.card.rates

    @property
    def web_search_picos(self) -> int:
        return (
            self.evidence.web_search_picos
            if isinstance(self.evidence, ClaudePricing)
            else 0
        )

    @property
    def amount(self) -> UsdPicos:
        return UsdPicos(sum(self.components().values()))

    def components(self) -> dict[str, int]:
        usage = self.usage
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

    def value(self) -> dict[str, object]:
        return {
            **pricing_value(self.evidence),
            "usd": dollars(self.amount),
            "components_usd": {
                key: dollars(value) for key, value in self.components().items()
            },
        }

    @classmethod
    def read(cls, value: object, usage: Tokens) -> "PricedUsage":
        return cls(read_pricing(value), usage)


@dataclass(frozen=True)
class Priced:
    usage: PricedUsage


@dataclass(frozen=True)
class Unpriced:
    reason: str


Price = Priced | Unpriced


def picos(value: object) -> int:
    """Read a materialized decimal projection without SQLite floating point."""
    amount = string(value, "stored USD estimate")
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
    return int(whole) * 10**12 + int(fraction)


def dollars(value: int) -> str:
    whole, fraction = divmod(value, 10**12)
    return f"{whole}.{fraction:012d}"


def quote(model: ModelId, tier: Tier, usage: Tokens) -> PricedUsage | None:
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
    return PricedUsage(
        CodexPricing(
            RateEvidence(
                model,
                long_context,
                rates,
                "2026-09-23",
                "https://developers.openai.com/api/docs/pricing",
            ),
            tier,
        ),
        usage,
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


def claude_quote(
    model: ModelId, modifiers: Modifiers, usage: Tokens
) -> PricedUsage | None:
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
    return PricedUsage(
        ClaudePricing(
            RateEvidence(
                model,
                False,
                rates,
                "2026-09-24",
                "https://platform.claude.com/docs/en/about-claude/pricing",
            ),
            PriceableModifiers.read(modifiers),
            10_000_000_000,
        ),
        usage,
    )
