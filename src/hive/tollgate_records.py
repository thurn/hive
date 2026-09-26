"""Validate the native CLI boundary into immutable, log-free candidate facts."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, record, sequence, string

TERMINAL: frozenset[str] = frozenset(
    {
        "promoted",
        "failed",
        "cancelled",
        "canceled",
        "merge-conflict",
        "superseded",
        "check-passed",
        "check-failed",
        "rejected",
        "externally-integrated",
        "dependency-failed",
        "infrastructure-exhausted",
    }
)
STATES: frozenset[str] = TERMINAL | {
    "constructing",
    "promoted-local-push-pending",
    "queued",
    "running",
    "ready",
    "blocked",
    "pending",
    "draining",
    "preparing",
    "promoting",
    "awaiting-approval",
}


def identifier(value: object) -> str:
    try:
        text = string(value, "Tollgate ID")
        parsed = UUID(text)
        if str(parsed) != text:
            raise ValueError("noncanonical UUID")
        return text
    except ValueError as error:
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid Tollgate ID") from error


def instant(value: object) -> str | None:
    if value is None:
        return None
    parts = sequence(value, "Tollgate timestamp")
    if len(parts) != 9:
        raise HiveError(ErrorCode.INVALID_RECORD, "Unknown Tollgate timestamp shape")
    values = [
        integer(p, "timestamp part", minimum=-23 if n == 6 else -59 if n > 6 else 0)
        for n, p in enumerate(parts)
    ]
    year, day, hour, minute, second, nano, oh, om, os = values
    try:
        if (
            not 1 <= day <= 366
            or not 0 <= nano < 1_000_000_000
            or abs(oh) > 23
            or abs(om) > 59
            or abs(os) > 59
        ):
            raise ValueError("timestamp range")
        signs = {1 if p > 0 else -1 for p in (oh, om, os) if p}
        if len(signs) > 1:
            raise ValueError("mixed offset signs")
        zone = timezone(timedelta(hours=oh, minutes=om, seconds=os))
        observed = datetime(
            year, 1, 1, hour, minute, second, nano // 1000, tzinfo=zone
        ) + timedelta(days=day - 1)
        if observed.year != year:
            raise ValueError("day of year")
        return observed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError) as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Invalid Tollgate timestamp"
        ) from error


def optional_text(value: object, limit: int = 256) -> str | None:
    return None if value is None else string(value, "Tollgate text", empty=True)[:limit]


@dataclass(frozen=True)
class Step:
    name: str
    result_class: str
    exit_code: int | None
    elapsed_ms: int


@dataclass(frozen=True)
class Attempt:
    id: str
    number: int
    state: str
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Candidate:
    id: str
    repository: str
    state: str
    branch: str | None
    subject: str | None
    reason: str | None
    submitted_at: str | None
    promoted_at: str | None
    updated_at: str
    attempts: tuple[Attempt, ...]
    kind: str
    retry_of: str | None

    def json(self) -> dict[str, object]:
        return {
            **asdict(self),
            "diagnostic_command": [
                "tg",
                "--repository",
                self.repository,
                "status",
                self.id,
                "--json",
            ],
        }


def decode(raw: object) -> Candidate:
    value = record(raw, "Tollgate candidate")
    item = record(value.get("item"), "Tollgate item")
    metadata = record(item.get("metadata"), "Tollgate metadata")
    state = string(item.get("state"), "candidate state")
    if state not in STATES:
        raise HiveError(ErrorCode.INVALID_RECORD, "Unknown Tollgate candidate state")
    attempts: list[Attempt] = []
    for entry in sequence(value.get("attempts"), "Tollgate attempts"):
        attempt = record(entry, "attempt")
        steps: list[Step] = []
        for entry_step in sequence(attempt.get("step_results"), "step results"):
            step = record(entry_step, "step")
            code = step.get("exit_code")
            steps.append(
                Step(
                    string(step.get("name"), "step name")[:256],
                    string(step.get("result_class"), "step result")[:64],
                    (
                        None
                        if code is None
                        else integer(code, "exit code", minimum=-(2**31))
                    ),
                    integer(step.get("elapsed_ms"), "elapsed milliseconds"),
                )
            )
        attempts.append(
            Attempt(
                identifier(attempt.get("id")),
                integer(attempt.get("attempt"), "attempt", minimum=1),
                string(attempt.get("state"), "attempt state")[:64],
                instant(attempt.get("created_at")),
                instant(attempt.get("started_at")),
                instant(attempt.get("finished_at")),
                tuple(steps),
            )
        )
    submitted = instant(metadata.get("approved_at"))
    promoted: str | None = None
    times = [
        t
        for a in attempts
        for t in (a.created_at, a.started_at, a.finished_at)
        if t is not None
    ] + [t for t in (submitted, promoted) if t is not None]
    if not times:
        raise HiveError(ErrorCode.INVALID_RECORD, "Candidate has no observation time")
    reason = item.get("terminal_reason")
    if isinstance(reason, dict):
        reason = record(reason).get("kind")
    reason = optional_text(reason, 64)
    # Only an enum-shaped reason, never a provider's diagnostic message.
    if reason is not None and (not reason.replace("-", "").replace("_", "").isalnum()):
        reason = "unknown"
    return Candidate(
        identifier(item.get("id")),
        identifier(item.get("repository_id")),
        state,
        optional_text(metadata.get("branch")),
        optional_text(metadata.get("subject"), 500),
        reason,
        submitted,
        promoted,
        max(times),
        tuple(attempts),
        string(item.get("kind", "gate"), "candidate kind"),
        (
            None
            if item.get("retry_of_item_id") is None
            else identifier(item.get("retry_of_item_id"))
        ),
    )
