"""Private raw-event spool and explicit operator configuration; no event parsing."""

import json
import os
import secrets
import stat
import time
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record

MAX_SPOOL = 256 * 1024 * 1024
MAX_SPOOL_FILES = 65536


def directory(state: Path) -> Path:
    spool = state / "otlp-spool"
    if spool.is_symlink():
        raise ValueError("OTLP spool must not be a symlink")
    spool.mkdir(mode=0o700, parents=True, exist_ok=True)
    spool.chmod(0o700)
    return spool


def private_write(path: Path, body: bytes, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)


def secret(state: Path) -> str:
    state.mkdir(parents=True, exist_ok=True)
    path = state / "otlp-secret"
    try:
        private_write(path, secrets.token_hex(32).encode("ascii"), exclusive=True)
    except FileExistsError:
        pass
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("OTLP secret must be a regular file")
        os.fchmod(stream.fileno(), 0o600)
        value = stream.read(65)
    if len(value) != 64 or any(char not in b"0123456789abcdef" for char in value):
        raise ValueError("Invalid OTLP secret file")
    return value.decode("ascii")


def spool_bytes(spool: Path) -> int:
    total = 0
    for path in chain(spool.glob("*"), (spool / "rejected").glob("*")):
        try:
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        except FileNotFoundError:
            pass  # A source-selected ingester can remove committed files.
    return total


def rejected(state: Path, count: int = 1) -> None:
    path = directory(state) / "rejections.log"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "ab") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(
            (
                json.dumps({"at": datetime.now(UTC).isoformat(), "count": count}) + "\n"
            ).encode()
        )


def spool_body(state: Path, body: bytes) -> bool:
    spool = directory(state)
    count = sum(1 for _ in spool.glob("*.json"))
    if count >= MAX_SPOOL_FILES or spool_bytes(spool) + len(body) > MAX_SPOOL:
        rejected(state)
        return False
    name = f"{time.time_ns()}-{secrets.randbits(64)}"
    temporary = spool / f"{name}.tmp"
    try:
        private_write(temporary, body, exclusive=True)
        os.replace(temporary, spool / f"{name}.json")
    finally:
        temporary.unlink(missing_ok=True)
    return True


def listener_status(
    state: Path, *, port: int | None, listening: bool, error: str | None
) -> None:
    state.mkdir(parents=True, exist_ok=True)
    temporary = state / "otlp-status.tmp"
    value = {
        "port": port,
        "listening": listening,
        "error": error,
        "pid": os.getpid(),
        "at": datetime.now(UTC).isoformat(),
    }
    private_write(temporary, json.dumps(value).encode())
    os.replace(temporary, state / "otlp-status.json")


def status(state: Path) -> dict[str, object]:
    try:
        value = record(parse((state / "otlp-status.json").read_text()))
    except FileNotFoundError:
        value = {"port": None, "listening": False, "error": None}
    except (HiveError, OSError) as error:
        value = {"port": None, "listening": False, "error": str(error)}
    pid = value.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            value["listening"] = False
            value["error"] = (
                value.get("error") or "Listener process is no longer running"
            )
    return {
        "otlp_listener": value,
        "otlp_spool_bytes": spool_bytes(state / "otlp-spool"),
    }


def config(state: Path, port: int, target: Path | None) -> dict[str, object]:
    if not 1 <= port <= 65535:
        raise HiveError(ErrorCode.INVALID_INPUT, "OTLP port must be 1 through 65535")
    if target is not None:
        # Exclusive creation avoids overwriting an unrelated operator file.
        private_write(target.expanduser(), secret(state).encode(), exclusive=True)
    return {
        "code": "OtlpConfiguration",
        "secret_file": None if target is None else str(target.expanduser()),
        "settings": {
            "env": {
                "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
                "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"http://127.0.0.1:{port}/v1/logs",
                "OTEL_EXPORTER_OTLP_LOGS_HEADERS": "Authorization=Bearer <secret>",
                "OTEL_METRICS_INCLUDE_VERSION": "true",
            }
        },
    }
