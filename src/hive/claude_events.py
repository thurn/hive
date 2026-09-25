"""Allow-listed OTLP/JSON events cross one typed, immutable source boundary."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hive.claude_usage import counter, optional_text
from hive.errors import ErrorCode, HiveError
from hive.identity import ModelId, ResponseId, ThreadId
from hive.jsonvalue import record, sequence, string
from hive.thread_links import thread_id
from hive.usage import timestamp

ALLOWED: frozenset[str] = frozenset(
    {
        "session.id",
        "prompt.id",
        "event.timestamp",
        "event.sequence",
        "event.name",
        "request_id",
        "client_request_id",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cost_usd_micros",
        "duration_ms",
        "speed",
        "effort",
        "query_source",
        "agent.name",
        "skill.name",
        "plugin.name",
        "mcp_server.name",
        "mcp_tool.name",
        "app.version",
    }
)


def attributes(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for raw in sequence(value, "OTLP attributes"):
        item = record(raw, "OTLP attribute")
        key = string(item.get("key"), "OTLP attribute key")
        if key not in ALLOWED:
            continue
        if key in result:
            raise HiveError(ErrorCode.INVALID_RECORD, "Duplicate OTLP attribute")
        data = record(item.get("value"), "OTLP attribute value")
        if "stringValue" in data and len(data) == 1:
            result[key] = string(data["stringValue"], "OTLP string", empty=True)
        elif "intValue" in data and len(data) == 1:
            number = data["intValue"]
            if isinstance(number, str):
                if not number.isascii() or not number.isdigit() or len(number) > 19:
                    raise HiveError(ErrorCode.INVALID_RECORD, "Invalid OTLP integer")
                number = int(number)
            result[key] = counter(number, "OTLP integer")
        else:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Unsupported OTLP attribute value"
            )
    return result


@dataclass(frozen=True)
class Log:
    task: ThreadId
    occurred: datetime
    sequence: int
    kind: str
    version: str | None
    values: tuple[tuple[str, object], ...]


def decode(raw: object, resource: object) -> Log:
    data = record(raw, "OTLP log record")
    values = {**attributes(resource), **attributes(data.get("attributes", []))}
    task = thread_id(values.get("session.id"))
    if task is None:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "OTLP session must be a canonical UUID"
        )
    body = record(data.get("body", {}), "OTLP event body")
    name = string(
        values.get("event.name", body.get("stringValue")), "OTLP event name"
    ).removeprefix("claude_code.")
    if body.get("stringValue") not in (None, name, "claude_code." + name):
        raise HiveError(ErrorCode.INVALID_RECORD, "OTLP event names disagree")
    return Log(
        task,
        timestamp(values.get("event.timestamp")).astimezone(UTC),
        counter(values.get("event.sequence"), "event sequence"),
        name,
        optional_text(values.get("app.version"), "Claude version"),
        tuple(sorted(values.items())),
    )


@dataclass(frozen=True)
class Request:
    log: Log
    response: ResponseId
    request_id: str | None
    client_id: str | None
    model: ModelId
    prompt: str | None
    observed: datetime
    input: int
    cached: int
    writes: int
    output: int
    micros: int
    duration: int
    speed: str | None
    query_source: str | None
    agent_name: str | None
    skill: str | None
    plugin: str | None
    mcp_server: str | None
    mcp_tool: str | None
    effort: str | None


def request(log: Log) -> Request:
    values = dict(log.values)
    request_id = optional_text(values.get("request_id"), "request ID")
    client_id = optional_text(values.get("client_request_id"), "client request ID")
    if request_id is None and client_id is None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Missing event request identity")
    identity = (
        request_id
        if request_id is not None
        else "client:" + string(client_id, "client request ID")
    )
    if len(identity) > 512:
        raise HiveError(ErrorCode.INVALID_RECORD, "Oversized event request identity")
    duration = counter(values.get("duration_ms"), "request duration")
    try:
        observed = log.occurred - timedelta(milliseconds=duration)
    except OverflowError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Invalid event request time"
        ) from error
    return Request(
        log=log,
        response=ResponseId(identity),
        request_id=request_id,
        client_id=client_id,
        model=ModelId(string(values.get("model"), "event model")),
        prompt=optional_text(values.get("prompt.id"), "prompt ID"),
        observed=observed,
        input=counter(values.get("input_tokens"), "input_tokens"),
        cached=counter(values.get("cache_read_tokens"), "cache_read_tokens"),
        writes=counter(values.get("cache_creation_tokens"), "cache_creation_tokens"),
        output=counter(values.get("output_tokens"), "output_tokens"),
        micros=counter(values.get("cost_usd_micros"), "cost_usd_micros"),
        duration=duration,
        speed=optional_text(values.get("speed"), "speed"),
        query_source=optional_text(values.get("query_source"), "query_source"),
        agent_name=optional_text(values.get("agent.name"), "agent.name"),
        skill=optional_text(values.get("skill.name"), "skill.name"),
        plugin=optional_text(values.get("plugin.name"), "plugin.name"),
        mcp_server=optional_text(values.get("mcp_server.name"), "mcp_server.name"),
        mcp_tool=optional_text(values.get("mcp_tool.name"), "mcp_tool.name"),
        effort=optional_text(values.get("effort"), "effort"),
    )
