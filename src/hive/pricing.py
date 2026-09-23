"""Exact API-equivalent arithmetic; prices are observations, never billing truth."""

from dataclasses import dataclass

from hive.errors import ErrorCode, HiveError
from hive.identity import ModelId, UsdPicos
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

    def value(self) -> dict[str, object]:
        return {
            "input": self.input,
            "cached": self.cached,
            "cache_write": self.cache_write,
            "output": self.output,
        }


@dataclass(frozen=True)
class Quote:
    model: ModelId
    tier: Tier
    long_context: bool
    rates: Rates
    price_observed: str
    source: str
    amount: UsdPicos

    def value(self) -> dict[str, object]:
        return {
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
            tier = Tier(string(data.get("tier"), "stored tier"))
        except ValueError as error:
            raise HiveError(ErrorCode.INVALID_RECORD, "Unknown stored tier") from error
        return cls(
            ModelId(string(data.get("model"), "stored model")),
            tier,
            band,
            Rates(
                *(
                    integer(rates.get(k), k)
                    for k in ("input", "cached", "cache_write", "output")
                )
            ),
            string(data.get("price_observed"), "price observation"),
            string(data.get("source"), "price source"),
            UsdPicos(int(whole) * 10**12 + int(fraction)),
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
