"""Bounded evidence inputs and inclusive native CI/operation intervals."""

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, string
from hive.tollgate_report import boundary

MAX_BYTES = 2 * 1024 * 1024
CONTEXT = (
    "head_oid",
    "staged_tree_oid",
    "tested_oid",
    "worktree_path",
    "root_operation_id",
    "task_id",
    "codex_thread_id",
    "candidate_id",
    "buildset_id",
    "generation_id",
)


@dataclass(frozen=True)
class Evidence:
    source: str
    line: int
    value: dict[str, object]


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = float(value)
    except OverflowError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def read(
    paths: tuple[Path, ...],
) -> tuple[tuple[Evidence, ...], list[dict[str, object]], list[str]]:
    if not 1 <= len(paths) <= 16:
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Provide 1–16 explicit JSONL evidence files"
        )
    events: list[Evidence] = []
    sources: list[dict[str, object]] = []
    gaps: list[str] = []
    fingerprints: set[str] = set()
    record_fingerprints: set[str] = set()
    for path in paths:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Evidence must be a regular file"
                )
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HiveError(ErrorCode.INVALID_INPUT, f"Evidence exceeds 2 MiB: {path}")
        digest = hashlib.sha256(data).hexdigest()
        if digest in fingerprints:
            continue
        fingerprints.add(digest)
        source = str(path.resolve())
        sources.append(dict[str, object](path=source, sha256=digest, bytes=len(data)))
        lines = data.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                value = record(parse(line.decode()), "evidence record")
            except (HiveError, UnicodeError):
                if index == len(lines) - 1 and not line.endswith(b"\n"):
                    gaps.append(f"incomplete_final_record:{source}")
                    continue
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    f"Malformed evidence at {source}:{index+1}",
                ) from None
            if len(events) >= 20000:
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Evidence exceeds 20,000 records"
                )
            fingerprint = hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest()
            if fingerprint not in record_fingerprints:
                record_fingerprints.add(fingerprint)
                events.append(Evidence(source, index + 1, value))
    return tuple(events), sources, gaps


def identity(value: object) -> dict[str, object]:
    result = record(value) if isinstance(value, dict) else {}
    return {key: result.get(key) for key in ("host", "pid", "birth")}


def normalize(
    events: tuple[Evidence, ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    contexts: dict[str, dict[str, object]] = {}
    owners: dict[str, dict[str, object]] = {}
    for event in events:
        value = event.value
        if value.get("event") in ("ci.run_started", "operation.started"):
            context = (
                record(value.get("context") or {})
                if value.get("event") == "operation.started"
                else value
            )
            key = str(value.get("operation_id", value.get("run_id", "")))
            contexts[key] = {
                **contexts.get(key, {}),
                **{k: context[k] for k in CONTEXT if context.get(k) is not None},
            }
            if value.get("event") == "operation.started":
                owners[key] = identity(value.get("process"))
    spans: list[dict[str, object]] = []
    queues: list[dict[str, object]] = []
    starts = {
        (
            str(e.value.get("run_id")),
            str(
                e.value.get(
                    "span_id", e.value.get("run_span_id", e.value.get("run_id"))
                )
            ),
        ): e
        for e in events
        if e.value.get("event") in {"ci.run_started", "ci.step_started"}
    }
    finishes = {
        (
            str(e.value.get("run_id")),
            str(
                e.value.get(
                    "span_id", e.value.get("run_span_id", e.value.get("run_id"))
                )
            ),
        ): e
        for e in events
        if e.value.get("event") in {"ci.run_finished", "ci.step_finished"}
    }
    gaps = [
        "terminal_unobserved:" + ":".join(key) for key in starts if key not in finishes
    ]
    seen: dict[tuple[str, ...], dict[str, object]] = {}
    for evidence in events:
        value = evidence.value
        event = value.get("event")
        if not isinstance(event, str):
            continue
        if event not in {
            "ci.run_finished",
            "ci.step_finished",
            "process.finished",
            "resource.queued",
            "resource.waiting",
            "resource.acquired",
            "resource.released",
        }:
            continue
        at = boundary(string(value.get("timestamp"), "event timestamp"))
        operation = str(value.get("operation_id", value.get("run_id", "")))
        if not operation:
            gaps.append(f"missing_run_identity:{evidence.source}:{evidence.line}")
            continue
        owner = identity(value.get("owner", value.get("process")))
        if owner["host"] is None:
            owner = owners.get(operation, owner)
        context = contexts.get(operation, contexts.get(str(value.get("run_id")), {}))
        provenance: dict[str, object] = dict[str, object](
            source=evidence.source,
            line=evidence.line,
            operation_id=operation,
            context=context,
            owner=owner,
        )
        if event.startswith("resource."):
            resource = string(value.get("resource"), "resource")
            queues.append(
                dict[str, object](
                    **provenance,
                    at=at.isoformat(),
                    event=event,
                    resource=resource,
                    units=value.get("units"),
                    capacity=value.get("capacity"),
                    held_units=value.get("held_units"),
                    queue_position=value.get("queue_position"),
                    queue_depth=value.get("queue_depth"),
                    older_pids=value.get("older_pids"),
                    inherited=value.get("inherited", False),
                )
            )
        duration_key = (
            "queue_duration_ms"
            if event == "resource.acquired"
            else "held_duration_ms" if event == "resource.released" else "duration_ms"
        )
        duration = number(value.get(duration_key))
        if event in ("resource.queued", "resource.waiting"):
            continue
        if duration is None:
            gaps.append(f"duration_unknown:{evidence.source}:{evidence.line}")
            continue
        if duration > 365 * 86400 * 1000:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Resource interval exceeds one year"
            )
        key = (
            operation,
            str(value.get("span_id", value.get("run_span_id", ""))),
            event,
            at.isoformat(),
            str(value.get("resource", "")),
            json.dumps(owner, sort_keys=True),
        )
        previous = seen.get(key)
        if previous is not None:
            if previous != value:
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    "Conflicting records for the same resource interval",
                )
            continue
        seen[key] = value
        cpu_user, cpu_system = number(value.get("cpu_user_ms")), number(
            value.get("cpu_system_ms")
        )
        scope = value.get("cpu_scope")
        cpu = (
            (cpu_user + cpu_system) / 1000
            if cpu_user is not None
            and cpu_system is not None
            and isinstance(scope, str)
            else None
        )
        kind = {
            "ci.run_finished": "ci_run",
            "ci.step_finished": "ci_step",
            "process.finished": "process",
            "resource.acquired": "nested_wait",
            "resource.released": "nested_held",
        }[event]
        spans.append(
            dict[str, object](
                **provenance,
                id=":".join(key),
                kind=kind,
                name=value.get("name", value.get("resource", value.get("executable"))),
                parent=value.get("parent_span_id"),
                start=(at - timedelta(milliseconds=duration)).isoformat(),
                end=at.isoformat(),
                wall_seconds=duration / 1000,
                cpu_seconds=cpu,
                cpu_scope=scope,
                cpu_per_wall=(
                    cpu / (duration / 1000)
                    if cpu is not None and duration > 0
                    else None
                ),
                max_rss_bytes=number(value.get("max_rss")),
                inclusive=True,
                inherited=value.get("inherited", False),
                coverage=(
                    "Measured inclusive CPU; parallel workers/still-running children may be missing"
                    if cpu is not None
                    else "CPU coverage unknown"
                ),
            )
        )
    return spans, queues, gaps
