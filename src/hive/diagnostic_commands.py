"""Parse observed command metadata without evaluating scripts or retaining text."""

import ast
import hashlib
import json
import re
import shlex
from dataclasses import dataclass

from hive.jsonvalue import parse, record


@dataclass(frozen=True)
class Command:
    ordinal: int
    exit_code: int | None
    wall_ms: int | None
    command_hash8: str
    command_kind: str


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def kind(command: str) -> str:
    command = command.strip()
    try:
        words = shlex.split(command)
    except ValueError:
        words = []
    if words and words[0].rsplit("/", 1)[-1] == "tg":
        index = 1
        while index < len(words) and words[index].startswith("-"):
            index += (
                2
                if words[index] in {"--repository", "-r", "--config", "--socket"}
                else 1
            )
        verb = words[index] if index < len(words) else ""
        if verb == "wait" or verb == "approve" and "--wait" in words[index + 1 :]:
            return "tg_wait"
        return "tg_candidate" if verb == "candidate" else "tg_other"
    patterns = (
        (r"(?:\S*/)?tg\s+(?:--\S+\s+)*(?:wait|approve\b.*--wait)\b", "tg_wait"),
        (r"(?:\S*/)?tg\s+(?:--\S+\s+)*candidate\b", "tg_candidate"),
        (r"(?:\S*/)?tg\b", "tg_other"),
        (r"(?:\S*/)?sleep\b", "sleep"),
        (
            r"(?:\S*/)?(?:pytest|vitest|jest)\b|(?:npm|pnpm)\s+(?:run\s+)?test\b|(?:\S*/)?python[\d.]*\s+-m\s+(?:unittest|pytest)\b|(?:\./)?scripts/check",
            "test",
        ),
    )
    return next(
        (name for pattern, name in patterns if re.match(pattern, command)), "other"
    )


def commands(input_text: str) -> tuple[str, ...]:
    try:
        value: object = json.loads(input_text)
    except ValueError:
        value = None
    if isinstance(value, dict):
        value = value.get("cmd", value.get("command"))
        if isinstance(value, str):
            return (value,)
    result: list[str] = []
    # Only literal cmd fields; dynamic JavaScript is never executed or guessed.
    for match in re.finditer(
        r"""(?:cmd|command)\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""", input_text
    ):
        try:
            value = ast.literal_eval(match[1])
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str):
            result.append(value)
    return tuple(result)


def chunks(output: str, uses: tuple[str, ...]) -> tuple[Command, ...] | None:
    # Native exec outputs concatenate a transport preamble and JSON result blocks.
    if "Script completed" not in output and not output.lstrip().startswith("{"):
        return None
    tail = output.split("Output:\n", 1)[-1].strip()
    decoder = json.JSONDecoder()
    results: list[Command] = []
    while tail:
        try:
            _, end = decoder.raw_decode(tail)
            value = record(parse(tail[:end]), "command chunk")
        except (ValueError, TypeError):
            return None
        from hive.errors import HiveError

        try:
            code = value.get("exit_code")
            wall = value.get("wall_time_seconds")
            if code is not None and (
                not isinstance(code, int) or isinstance(code, bool)
            ):
                return None
            if wall is not None and (
                not isinstance(wall, (float, int)) or isinstance(wall, bool) or wall < 0
            ):
                return None
            if code is None and wall is None:
                return None
            wall_ms = None if wall is None else round(wall * 1000)
            if (
                wall_ms is not None
                and wall_ms > 2**63 - 1
                or code is not None
                and abs(code) > 2**31 - 1
            ):
                return None
            ordinal = len(results)
            command = uses[ordinal] if ordinal < len(uses) else ""
            results.append(
                Command(
                    ordinal,
                    code,
                    wall_ms,
                    digest(command),
                    kind(command),
                )
            )
        except (HiveError, OverflowError, ValueError):
            return None
        tail = tail[end:].strip()
    return tuple(results) if results else None
