"""Fixed, validated loopback routes; arguments never pass through a shell."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from hive.bead_queries import BEAD

TREE: re.Pattern[str] = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
PROJECT: re.Pattern[str] = re.compile(r"[A-Za-z0-9._-]{1,128}")


@dataclass(frozen=True)
class Route:
    kind: str
    arguments: tuple[str, ...] = ()
    tree: str = ""
    asset: str = ""


def uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def route(target: str) -> Route | None:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment or not target.startswith("/"):
        return None
    path = unquote(parsed.path, errors="strict")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]*", path):
        return None
    parts = path[1:].split("/")
    if path != "/" and any(p in {"", ".", ".."} for p in parts):
        return None
    if parts[0] == "c":
        if len(parts) < 3 or TREE.fullmatch(parts[1]) is None or parsed.query:
            return None
        return Route("static", tree=parts[1], asset="/".join(parts[2:]))
    client = (
        path == "/"
        or (
            len(parts) == 2
            and parts[0] == "bead"
            and BEAD.fullmatch(parts[1]) is not None
        )
        or (len(parts) == 2 and parts[0] == "session" and uuid(parts[1]))
        or (
            len(parts) == 3
            and parts[0] == "ledger"
            and parts[1] in {"small_tails", "unattributable"}
            and PROJECT.fullmatch(parts[2]) is not None
        )
    )
    if client:
        return Route("page")
    if len(parts) < 2 or parts[0] != "api":
        return None
    name = parts[1]
    allowed: set[str]
    if name == "feed" and len(parts) == 2:
        allowed = {
            "window",
            "project",
            "role",
            "state",
            "q",
            "active",
            "older-completed",
            "cursor",
        }
        args = ["feed"]
    elif name == "bead" and len(parts) == 3 and BEAD.fullmatch(parts[2]):
        allowed = {"requests", "cursor", "sort"}
        args = [name, parts[2]]
    elif name == "session" and len(parts) == 3 and uuid(parts[2]):
        allowed = {"tail"}
        args = [name, parts[2]]
    elif (
        name == "ledger"
        and len(parts) == 4
        and parts[2] in {"small_tails", "unattributable"}
        and PROJECT.fullmatch(parts[3])
    ):
        allowed = set()
        args = [name, parts[2], parts[3]]
    elif name in {"excerpt", "ci-log", "status"} and len(parts) == 2:
        allowed = (
            {"thread", "agent", "call", "event"}
            if name == "excerpt"
            else {"candidate", "step"} if name == "ci-log" else set()
        )
        args = [name]
    else:
        return None
    values = parse_qsl(
        parsed.query, keep_blank_values=True, max_num_fields=16, errors="strict"
    )
    if len({k for k, _ in values}) != len(values):
        return None
    for key, value in values:
        if (
            key not in allowed
            or len(value) > (8192 if key == "cursor" else 1024)
            or any(ord(c) < 32 for c in value)
        ):
            return None
        if key in {"active", "older-completed", "requests", "tail"}:
            if value not in {"", "1", "true"}:
                return None
            args.append("--" + key)
        else:
            if key in {"thread", "candidate"} and not uuid(value):
                return None
            if key == "cursor" and re.fullmatch(r"[A-Za-z0-9_=-]+", value) is None:
                return None
            args.append("--" + key + "=" + value)
    return Route("api", tuple(args))
