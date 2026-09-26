"""Windowed submission cohorts with explicit, conservative coverage."""

import sqlite3
from datetime import UTC, datetime, timedelta

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, sequence, string
from hive.tollgate_observation import status
from hive.tollgate_records import TERMINAL
from hive.usage_store import row


def boundary(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(UTC)
    except ValueError as error:
        raise HiveError(
            ErrorCode.INVALID_INPUT,
            "Report bounds require ISO timestamps with timezones",
        ) from error


def report(
    connection: sqlite3.Connection, project: str, start: str, end: str
) -> dict[str, object]:
    beginning, ending = boundary(start), boundary(end)
    if beginning >= ending:
        raise HiveError(ErrorCode.INVALID_INPUT, "Report start must precede end")
    start, end = beginning.isoformat(), ending.isoformat()
    health = status(connection)
    coverage_row: object = connection.execute(
        "SELECT observed,payload FROM tollgate_coverage WHERE project=?", (project,)
    ).fetchone()
    coverage: dict[str, object] = {"complete": False}
    gaps = ["native_history_bounded_no_exhaustive_cursor"]
    if coverage_row is None:
        gaps.append("repository_not_observed")
    else:
        observed, payload = row(coverage_row, 2)
        coverage.update(record(parse(string(payload, "coverage"))))
        coverage["observed_at"] = observed
        if boundary(string(observed, "observed")) < datetime.now(UTC) - timedelta(
            minutes=5
        ):
            gaps.append("repository_snapshot_stale")
    if (
        connection.execute(
            "SELECT 1 FROM tollgate_repositories WHERE project=?", (project,)
        ).fetchone()
        is None
        and coverage_row is not None
    ):
        gaps.append("repository_not_in_latest_snapshot")
    if health["tollgate_error"] is not None:
        gaps.append(string(health["tollgate_error"], "poll error"))
    pending: object = connection.execute(
        "SELECT COUNT(*) FROM tollgate_pending p JOIN tollgate_repositories r ON r.repository=p.repository LEFT JOIN tollgate_candidates c ON c.candidate=p.candidate WHERE r.project=? AND c.candidate IS NULL",
        (project,),
    ).fetchone()
    unresolved = row(pending, 1)[0]
    if unresolved:
        gaps.append("candidate_details_unresolved")
    coverage["unresolved_candidates"] = unresolved
    coverage["gaps"] = gaps
    counts = {
        key: 0
        for key in (
            "promoted",
            "conflict",
            "validation_failed",
            "canceled",
            "other_terminal",
            "in_progress",
        )
    }
    unique = retries = attempts = attempt_retries = checks = unknown_time = legacy = 0
    sources: object = connection.execute(
        "SELECT payload FROM tollgate_candidates WHERE project=?", (project,)
    ).fetchall()
    for source in sequence(sources, "candidate records"):
        value = record(parse(string(row(source, 1)[0], "candidate payload")))
        submitted = value.get("submitted_at")
        if submitted is None:
            unknown_time += 1
            continue
        if not start <= string(submitted, "submission") < end:
            continue
        if "kind" not in value:
            legacy += 1
            continue
        if value["kind"] != "gate":
            checks += 1
            continue
        unique += 1
        retries += value.get("retry_of") is not None
        candidate_attempts = {
            string(record(a).get("id"), "attempt id")
            for a in sequence(value.get("attempts"), "attempts")
        }
        attempts += len(candidate_attempts)
        attempt_retries += max(0, len(candidate_attempts) - 1)
        state = string(value.get("state"), "state")
        if state == "promoted":
            outcome = "promoted"
        elif state == "merge-conflict":
            outcome = "conflict"
        elif state in {"cancelled", "canceled"}:
            outcome = "canceled"
        elif (
            state in {"failed", "check-failed"}
            and value.get("reason") == "voting-validation-failed"
        ):
            outcome = "validation_failed"
        else:
            outcome = "other_terminal" if state in TERMINAL else "in_progress"
        counts[outcome] += 1
    if legacy:
        gaps.append("legacy_candidate_metadata")
    if unknown_time:
        gaps.append("submission_time_unknown")
    return dict(
        code="TollgateOutcomes",
        project=project,
        window_start=start,
        window_end=end,
        window_basis="submitted_at",
        end_exclusive=True,
        outcomes_as_of="latest_observation",
        unique_candidates=unique,
        counts=counts,
        retry_candidates=retries,
        validation_attempts=attempts,
        additional_validation_attempts=attempt_retries,
        excluded_checks=checks,
        legacy_candidates=legacy,
        unknown_submission_time=unknown_time,
        coverage=coverage,
        **health,
    )
