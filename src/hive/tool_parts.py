"""Only prompt-visible byte lengths and tool identities leave native transcripts."""

import json
from dataclasses import dataclass

from hive.jsonvalue import record, sequence, string


@dataclass(frozen=True)
class Part:
    kind: str
    ref: str | None
    name: str | None
    size: int


def size(value: object) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def content(value: object, *, assistant: bool) -> tuple[Part, ...]:
    if isinstance(value, str):
        return (Part("text" if assistant else "user", None, None, size(value)),)
    result: list[Part] = []
    for raw in sequence(value, "content blocks"):
        block = record(raw, "content block")
        kind = string(block.get("type"), "content type")
        if kind == "tool_use":
            result.append(
                Part(
                    kind,
                    string(block.get("id"), "tool ID"),
                    string(block.get("name"), "tool name"),
                    size(block.get("input", {})),
                )
            )
        elif kind == "tool_result":
            result.append(
                Part(
                    kind,
                    string(block.get("tool_use_id"), "result tool ID"),
                    None,
                    size(block.get("content", "")),
                )
            )
        elif kind in {"thinking", "redacted_thinking"}:
            result.append(Part("thinking", None, None, 0))
        elif kind == "text":
            text = string(block.get("text", ""), "text", empty=True)
            # Reminders arrive inside prompt-visible user text.
            category = (
                "text"
                if assistant
                else "reminder" if "<system-reminder>" in text else "user"
            )
            result.append(Part(category, None, None, size(text)))
        else:
            result.append(Part("attachment", None, kind, size(block)))
    return tuple(result)


def parts(raw: dict[str, object]) -> tuple[Part, ...]:
    kind = raw.get("type")
    if kind in {"assistant", "user"}:
        message = record(raw.get("message"), "message")
        return content(message.get("content", []), assistant=kind == "assistant")
    if kind == "attachment":
        attachment = record(raw.get("attachment", {}), "attachment")
        rendered = attachment.get("rendered", attachment.get("content"))
        if rendered is not None:
            return (
                Part(
                    "attachment",
                    None,
                    string(attachment.get("type", "unknown"), "attachment kind"),
                    size(rendered),
                ),
            )
    return ()
