"""Reproducible half-open resource windows without summing inclusive CPU phases."""

import json
from datetime import datetime
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import record, string
from hive.resource_records import Evidence, normalize, read
from hive.tollgate_report import boundary


def interval(value: dict[str, object]) -> tuple[datetime, datetime]:
    start, end = boundary(string(value.get("start"), "start")), boundary(
        string(value.get("end"), "end")
    )
    if start > end:
        raise HiveError(ErrorCode.INVALID_RECORD, "Evidence interval runs backwards")
    return start, end


def window(
    events: tuple[Evidence, ...], project: str, start: str, end: str
) -> dict[str, object]:
    first, last = boundary(start), boundary(end)
    if first >= last:
        raise HiveError(ErrorCode.INVALID_INPUT, "Report start must precede end")
    spans, queues, gaps = normalize(events)
    host_samples: list[dict[str, object]] = []
    gate_samples: list[dict[str, object]] = []
    for event in events:
        sample = event.value
        if sample.get("code") != "ResourceSample":
            continue
        if sample.get("schema") != 1 or sample.get("project") != project:
            gaps.append(f"sample_scope_or_schema_mismatch:{event.source}:{event.line}")
            continue
        for kind, target in (("host", host_samples), ("gate", gate_samples)):
            value = record(sample.get(kind), kind)
            beginning, ending = interval(value)
            if beginning < last and ending > first:
                if value.get("error") is not None:
                    gaps.append(kind + "_sample_unavailable")
                target.append(
                    {
                        **value,
                        "source": event.source,
                        "line": event.line,
                        "fully_in_window": first <= beginning and ending <= last,
                        "overlap_seconds": (
                            min(last, ending) - max(first, beginning)
                        ).total_seconds(),
                    }
                )
    selected: list[dict[str, object]] = []
    for span in spans:
        beginning, ending = interval(span)
        if beginning >= last or ending <= first:
            continue
        selected.append(
            {
                **span,
                "fully_in_window": first <= beginning and ending <= last,
                "overlap_seconds": (
                    min(last, ending) - max(first, beginning)
                ).total_seconds(),
                "host_alignment": "same recorded host and overlapping capture bounds; no causal attribution",
                "host_samples": [
                    index
                    for index, sample in enumerate(host_samples)
                    if record(span["owner"]).get("host") is not None
                    and sample.get("host") == record(span["owner"]).get("host")
                    and interval(sample)[0] < ending
                    and interval(sample)[1] > beginning
                ],
            }
        )
    nested: dict[tuple[str, str, str], dict[str, object]] = {}
    for observation in sorted(queues, key=lambda q: str(q["at"])):
        at = boundary(string(observation["at"], "observation timestamp"))
        if at >= last:
            continue
        key = (
            string(observation["operation_id"], "operation"),
            string(observation["resource"], "resource"),
            json.dumps(observation["owner"], sort_keys=True),
        )
        nested[key] = {
            **observation,
            "age_at_window_end_seconds": (last - at).total_seconds(),
            "observed_in_window": at >= first,
            "state_basis": "Last recorded event only; not a live lock inspection",
        }
    if not host_samples:
        gaps.append("host_coverage_unknown")
    if not gate_samples:
        gaps.append("gate_queue_coverage_unknown")
    if not nested:
        gaps.append("nested_queue_coverage_unknown")
    if not any(s["kind"] in {"ci_run", "ci_step", "process"} for s in selected):
        gaps.append("execution_coverage_unknown")
    return dict[str, object](
        start=first.isoformat(),
        end=last.isoformat(),
        end_exclusive=True,
        spans=selected,
        host_samples=host_samples,
        gate_samples=gate_samples,
        nested_resource_observations=list(nested.values()),
        coverage=dict[str, object](
            complete=False,
            gaps=sorted(set(gaps)),
            cpu_unknown_execution_spans=sum(
                s["cpu_seconds"] is None
                and s["kind"] in {"ci_run", "ci_step", "process"}
                for s in selected
            ),
            partial_intervals=sum(not s["fully_in_window"] for s in selected),
        ),
        interpretation="Durations and CPU describe whole observed inclusive intervals; overlap is alignment only, never CPU proration. Do not sum CI runs, child steps, processes, waits or inherited leases. Sparse host samples and queue events cannot determine an optimal executor count.",
    )


def report(
    project: str,
    start: str,
    end: str,
    paths: tuple[Path, ...],
    compare_start: str | None = None,
    compare_end: str | None = None,
) -> dict[str, object]:
    if bool(compare_start) != bool(compare_end):
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Comparison requires both start and end"
        )
    events, sources, gaps = read(paths)
    result: dict[str, object] = dict[str, object](
        code="ResourceReport",
        schema=1,
        project=project,
        input_scope="Explicit operator-selected native CI/operation logs and Hive ResourceSample JSONL; no ambient directory scan",
        sources=sources,
        input_gaps=gaps,
        window=window(events, project, start, end),
    )
    if compare_start and compare_end:
        result["comparison"] = window(events, project, compare_start, compare_end)
    return result
