"""Per-response observations; cumulative counters are never billable events."""

from dataclasses import dataclass
from datetime import datetime

from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, CodexTurnId, Owner, ResponseId
from hive.jsonvalue import integer, record, string


@dataclass(frozen=True)
class Tokens:
    input: int
    cached_input: int
    cache_write_input: int
    output: int
    reasoning_output: int
    cache_write_1h_input: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input,
            self.cached_input,
            self.cache_write_input,
            self.output,
            self.reasoning_output,
            self.cache_write_1h_input,
        )
        if (
            any(isinstance(v, bool) or v < 0 for v in values)
            or self.cached_input > self.input
            or self.cached_input + self.cache_write_input + self.cache_write_1h_input
            > self.input
            or self.reasoning_output > self.output
        ):
            raise HiveError(ErrorCode.INVALID_RECORD, "Inconsistent token counters")

    def value(self) -> dict[str, object]:
        return {
            "input_tokens": self.input,
            "cached_input_tokens": self.cached_input,
            "cache_write_input_tokens": self.cache_write_input,
            "cache_write_1h_input_tokens": self.cache_write_1h_input,
            "output_tokens": self.output,
            "reasoning_output_tokens": self.reasoning_output,
        }


@dataclass(frozen=True)
class MissingUsage:
    pass


@dataclass(frozen=True)
class ResponseUsage:
    response: ResponseId
    owner: Owner
    observed: datetime
    tokens: Tokens | MissingUsage


def timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(string(value, "observation timestamp"))
    except ValueError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Invalid observation timestamp"
        ) from error
    if result.tzinfo is None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Observation time needs a timezone")
    return result


def tokens(value: object) -> Tokens:
    data = record(value, "response usage")
    result = Tokens(
        *(
            integer(data.get(k), k)
            for k in (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
            )
        ),
        cache_write_1h_input=integer(
            data.get("cache_write_1h_input_tokens", 0), "1h cache write tokens"
        ),
    )
    if (
        "total_tokens" in data
        and integer(data["total_tokens"], "total tokens")
        != result.input + result.output
    ):
        raise HiveError(ErrorCode.INVALID_RECORD, "Total tokens disagree with usage")
    return result


def decode(value: object, task: CodexTaskId) -> ResponseUsage | None:
    """Validate the deployed native token_usage_record shape at one boundary.

    Legacy token_count events and compaction copies contain cumulative or
    repeated usage. Ignore them rather than manufacturing response identities.
    """
    raw = record(value, "transcript record")
    kind = string(raw.get("type"), "transcript record type")
    if kind == "session_meta":
        identity = record(raw.get("payload"), "native session").get("id")
        if identity != task:
            raise HiveError(ErrorCode.INVALID_RECORD, "Transcript is for another task")
        return None
    if kind != "token_usage_record":
        return None
    data = record(raw.get("payload"), "native usage record")
    if data.get("thread_id") != task or data.get("session_id") != task:
        raise HiveError(ErrorCode.INVALID_RECORD, "Usage is for another native task")
    observed = MissingUsage() if data.get("usage") is None else tokens(data["usage"])
    if isinstance(observed, Tokens) and observed.input + observed.output > 2**63 - 1:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Native token usage exceeds signed 64-bit range"
        )
    return ResponseUsage(
        ResponseId(string(data.get("response_id"), "native response identity")),
        Owner(task, CodexTurnId(string(data.get("turn_id"), "native turn"))),
        timestamp(raw.get("timestamp")),
        observed,
    )
