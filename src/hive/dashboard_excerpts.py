"""On-demand native excerpts, identity checked and never retained."""

import hashlib
import os
import sqlite3

from hive.dashboard_process import run
from hive.dashboard_values import packed, rows
from hive.diagnostic_store import text
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.launch_context import LaunchContext
from hive.tollgate_records import identifier
from hive.transcript_chunks import MAX_LINE
from hive.usage import timestamp


def line(path: str, offset: object, device: object, inode: object) -> dict[str, object]:
    position = integer(offset, "offset")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        stat = os.fstat(stream.fileno())
        import stat as stat_module

        if (
            not stat_module.S_ISREG(stat.st_mode)
            or (stat.st_dev, stat.st_ino) != (device, inode)
            or stat.st_size <= position
        ):
            raise ValueError("Source was replaced or truncated")
        if position:
            stream.seek(position - 1)
            if stream.read(1) != b"\n":
                raise ValueError("Source offset no longer begins a record")
        stream.seek(position)
        content = stream.readline(MAX_LINE + 1)
        if len(content) > MAX_LINE or not content.endswith(b"\n"):
            raise ValueError("Record is incomplete or exceeds 256 KiB")
        return record(parse(content.decode()))


def block(
    raw: dict[str, object], host: object, identity: str, result: bool
) -> dict[str, object]:
    if host == "codex":
        value = record(raw.get("payload"))
        kinds = (
            {"function_call_output", "custom_tool_call_output"}
            if result
            else {"function_call", "custom_tool_call"}
        )
        if value.get("call_id") != identity or value.get("type") not in kinds:
            raise ValueError("Record no longer matches the tool call")
        return value
    message = record(raw.get("message"))
    for value in sequence(message.get("content"), "message content"):
        candidate = record(value)
        if (
            candidate.get("type") == ("tool_result" if result else "tool_use")
            and candidate.get("tool_use_id" if result else "id") == identity
        ):
            return candidate
    raise ValueError("Record no longer matches the tool call")


def clip(value: str, limit: int) -> str:
    return value.encode()[:limit].decode(errors="ignore")


def excerpt(
    connection: sqlite3.Connection,
    thread: str,
    call: str | None,
    event: str | None,
    agent: str | None = None,
) -> dict[str, object]:
    try:
        if call is not None:
            matches = rows(
                connection,
                "SELECT * FROM tool_calls WHERE thread=? AND call_id=? AND (? IS NULL OR agent=?)",
                (thread, call, agent, agent),
            )
        else:
            matches = rows(
                connection,
                "SELECT * FROM session_events WHERE thread=? AND id=?",
                (thread, event),
            )
        if len(matches) != 1:
            raise ValueError("Diagnostic missing or ambiguous; select its subagent")
        value = matches[0]
        sources = rows(
            connection,
            "SELECT path FROM sources WHERE task=? AND file=?",
            (thread, value["file"]),
        )
        if len(sources) != 1:
            raise ValueError("Source path is no longer observed")
        path = string(sources[0]["path"], "source")
        if value["host"] == "codex":
            task = value["agent"] or thread
            indexed = rows(
                connection, "SELECT path FROM project_sessions WHERE thread=?", (task,)
            )
            if indexed and indexed[0]["path"] != path:
                raise ValueError("Native rollout path changed")
        if call is not None:
            use = block(
                line(
                    path, value["use_offset"], value["use_device"], value["use_inode"]
                ),
                value["host"],
                call,
                False,
            )
            input_value = use.get("arguments", use.get("input", ""))
            content = (
                input_value if isinstance(input_value, str) else packed(input_value)
            )
            if hashlib.sha256(content.encode()).hexdigest()[:8] != value["input_hash8"]:
                # Claude uses json.dumps with the normal spaces, unlike packed().
                import json

                if (
                    hashlib.sha256(
                        json.dumps(input_value, sort_keys=True).encode()
                    ).hexdigest()[:8]
                    != value["input_hash8"]
                ):
                    raise ValueError("Tool input changed")
            result_text = ""
            if value["result_offset"] is not None:
                result = block(
                    line(
                        path,
                        value["result_offset"],
                        value["result_device"],
                        value["result_inode"],
                    ),
                    value["host"],
                    call,
                    True,
                )
                result_text = text(result.get("output", result.get("content", "")))
                if "tool-results/" in result_text and ".txt" in result_text:
                    return dict[str, object](
                        code="excerpt_unavailable",
                        reason="Result is an external tool-results pointer",
                        pointer=clip(result_text, 2048),
                    )
                if value["host"] == "codex" and value["tool"] in {
                    "exec",
                    "functions.exec",
                }:
                    result_text = failing_output(result_text)
            return dict[str, object](
                tool=value["tool"],
                input_summary=" ".join(content.split())[:300],
                result=clip(result_text, 2048),
                pending=value["result_offset"] is None,
            )
        raw = line(path, value["offset"], value["device"], value["inode"])
        payload = (
            record(raw["payload"]) if isinstance(raw.get("payload"), dict) else raw
        )
        kind = value["kind"]
        native_kind = payload.get("type")
        valid = (
            (kind == "api_error" and raw.get("subtype") == "api_error")
            or (
                kind == "compaction"
                and (
                    native_kind in {"context_compacted", "compaction"}
                    or raw.get("subtype")
                    in {"compact_boundary", "microcompact_boundary"}
                )
            )
            or (
                kind == "interrupt"
                and (
                    native_kind == "turn_aborted"
                    or "[Request interrupted by user"
                    in text(raw.get("message", payload))
                )
            )
        )
        if not valid or timestamp(raw.get("timestamp")) != timestamp(value["at"]):
            raise ValueError("Event has no independently verifiable text excerpt")
        content = text(raw.get("message", payload))
        return dict[str, object](kind=kind, result=clip(content, 2048))
    except (HiveError, OSError, ValueError, UnicodeError) as error:
        return dict[str, object](code="excerpt_unavailable", reason=str(error))


def failing_output(content: str) -> str:
    # Native exec can emit multiple JSON objects in one output. Only validated
    # JSON objects are inspected; strings containing code are never executed.
    import json

    decoder = json.JSONDecoder()
    remaining = content.lstrip()
    # The native wrapper precedes concatenated JSON chunks with an Output line.
    if "\nOutput:\n" in remaining:
        remaining = remaining.split("\nOutput:\n", 1)[1].lstrip()
    while remaining:
        try:
            raw: object
            raw, end = decoder.raw_decode(remaining)
        except ValueError:
            break
        if isinstance(raw, dict):
            chunk = record(raw)
            if chunk.get("exit_code") not in (None, 0) and isinstance(
                chunk.get("output"), str
            ):
                return string(chunk["output"], "output", empty=True)
        remaining = remaining[end:].lstrip()
    return content


def ci_log(
    connection: sqlite3.Connection,
    context: LaunchContext,
    candidate: str,
    step: str,
    attempt: str | None = None,
) -> dict[str, object]:
    identifier(candidate)
    if not step or len(step) > 256 or "\0" in step:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid CI step")
    values = rows(
        connection,
        "SELECT repository,payload FROM tollgate_candidates WHERE candidate=?",
        (candidate,),
    )
    if not values:
        raise HiveError(ErrorCode.NOT_FOUND, "Candidate not observed")
    payload = record(parse(string(values[0]["payload"], "candidate")))
    if attempt is not None:
        identifier(attempt)
    known = {
        record(s)["name"]
        for a in sequence(payload["attempts"], "attempts")
        if attempt is None or record(a)["id"] == attempt
        for s in sequence(record(a)["steps"], "steps")
    }
    if step not in known:
        raise HiveError(ErrorCode.NOT_FOUND, "Step not observed")
    output = run(
        (
            "tg",
            "--no-launch",
            "--repository",
            string(values[0]["repository"], "repository"),
            "logs",
            candidate,
            "--step=" + step,
            *(("--buildset=" + attempt,) if attempt else ()),
        ),
        context.repository,
        5,
    )
    return dict[str, object](
        candidate=candidate,
        attempt=attempt,
        step=step,
        tail=output.content[-4096:].decode(errors="replace"),
        truncated=output.truncated or len(output.content) > 4096,
    )
