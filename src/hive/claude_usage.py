"""Validate undocumented Claude Code transcript records at the source boundary."""

from dataclasses import dataclass

from hive.errors import ErrorCode, HiveError
from hive.identity import AgentId, Host, ModelId, Owner, ResponseId, ThreadId, TurnId
from hive.jsonvalue import integer, record, sequence, string
from hive.usage import ResponseUsage, Tokens, timestamp


def optional_text(value: object, name: str) -> str | None:
    return None if value is None else string(value, name)


def counter(value: object, name: str) -> int:
    result = integer(value, name)
    if result > 2**63 - 1:
        raise HiveError(ErrorCode.INVALID_RECORD, f"{name} exceeds signed 64-bit range")
    return result


@dataclass(frozen=True)
class Modifiers:
    speed: str | None
    service_tier: str | None
    inference_geo: str | None
    web_searches: int

    @classmethod
    def read(cls, value: object) -> "Modifiers":
        data = record(value, "request modifiers")
        return cls(
            optional_text(data.get("speed"), "speed"),
            optional_text(data.get("service_tier"), "service tier"),
            optional_text(data.get("inference_geo"), "inference geography"),
            counter(data.get("web_searches", 0), "web searches"),
        )

    @property
    def key(self) -> str:
        return "/".join(
            value if value is not None else "unknown"
            for value in (self.speed, self.service_tier, self.inference_geo)
        )

    def value(self) -> dict[str, object]:
        return {
            "speed": self.speed,
            "service_tier": self.service_tier,
            "inference_geo": self.inference_geo,
            "web_searches": self.web_searches,
        }


@dataclass(frozen=True)
class ClaudeResponse:
    usage: ResponseUsage
    model: ModelId
    modifiers: Modifiers
    complete: bool
    skill: str | None
    flags: tuple[str, ...]


def decode(value: object, thread: ThreadId) -> ClaudeResponse | None:
    raw = record(value, "Claude transcript record")
    if "sessionId" in raw and raw["sessionId"] != thread:
        raise HiveError(ErrorCode.INVALID_RECORD, "Usage is for another native task")
    if raw.get("type") != "assistant":
        return None
    message = record(raw.get("message"), "Claude message")
    model = ModelId(string(message.get("model"), "Claude model"))
    usage = record(message.get("usage"), "Claude usage")
    uncached, writes, cached, output = (
        counter(usage.get(name), name)
        for name in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    )
    if model == "<synthetic>" and not (uncached + writes + cached + output):
        return None
    flags: list[str] = []
    if "cache_creation" in usage:
        split = record(usage["cache_creation"], "cache creation split")
        five = counter(split.get("ephemeral_5m_input_tokens"), "5m cache writes")
        hour = counter(split.get("ephemeral_1h_input_tokens"), "1h cache writes")
        if five + hour != writes:
            raise HiveError(ErrorCode.INVALID_RECORD, "Cache TTL counters disagree")
    else:
        five, hour = writes, 0
        flags.append("ttl_assumed")
    details = record(usage.get("output_tokens_details", {}), "output details")
    thinking = counter(details.get("thinking_tokens", 0), "thinking tokens")
    if "thinking_tokens" not in details:
        flags.append("thinking_unmeasured")
    if "iterations" in usage:
        iterations = sequence(usage["iterations"], "Claude iterations")
        names = (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
        totals = [0] * 4
        supported = bool(iterations)
        for value in iterations:
            try:
                iteration = record(value, "Claude iteration")
                supported &= iteration.get("type") == "message"
                if "model" in iteration:
                    supported &= iteration["model"] == model
                for index, name in enumerate(names):
                    totals[index] += counter(iteration.get(name), name)
            except HiveError:
                supported = False
        if not supported or totals != [uncached, writes, cached, output]:
            flags.append("unsupported_iteration")
    tools = record(usage.get("server_tool_use", {}), "server tools")
    modifiers = Modifiers(
        optional_text(usage.get("speed"), "speed"),
        optional_text(usage.get("service_tier"), "service tier"),
        optional_text(usage.get("inference_geo"), "inference geography"),
        counter(tools.get("web_search_requests", 0), "web searches"),
    )
    agent = optional_text(raw.get("agentId"), "Claude agent")
    turn = optional_text(raw.get("promptId"), "Claude prompt")
    prompt = uncached + writes + cached
    if prompt > 2**63 - 1:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Claude prompt exceeds signed 64-bit range"
        )
    return ClaudeResponse(
        ResponseUsage(
            ResponseId(
                string(raw.get("requestId", message.get("id")), "Claude request ID")
            ),
            Owner(
                thread,
                None if turn is None else TurnId(turn),
                Host.CLAUDE,
                None if agent is None else AgentId(agent),
            ),
            timestamp(raw.get("timestamp")),
            Tokens(prompt, cached, five, output, thinking, hour),
        ),
        model,
        modifiers,
        message.get("stop_reason") is not None,
        optional_text(raw.get("attributionSkill"), "attribution skill"),
        tuple(flags),
    )
