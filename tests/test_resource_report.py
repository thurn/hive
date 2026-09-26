"""Public commands correlate recorded native evidence without changing its sources."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_bead_cost import command
from test_contention import hive
from test_tollgate_observation import REPO, fake

from hive.jsonvalue import record, sequence


def objects(value: object) -> list[dict[str, object]]:
    return [record(v) for v in sequence(value, "records")]


class ResourceTests(unittest.TestCase):
    def test_windows_preserve_inclusive_cpu_wait_owners_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def at(second: int) -> str:
                return f"2026-09-26T00:00:{second:02d}Z"

            owner = dict(host="host-a", pid=42, birth="process-1")
            records = [
                dict(
                    event="ci.run_started",
                    timestamp=at(0),
                    run_id="run",
                    head_oid="source",
                    staged_tree_oid="tree",
                ),
                dict(
                    event="operation.started",
                    timestamp=at(0),
                    operation_id="run",
                    process=owner,
                    context=dict(candidate_id="candidate", buildset_id="build"),
                ),
                dict(
                    event="resource.queued",
                    timestamp=at(1),
                    operation_id="run",
                    resource="compiler",
                    owner=owner,
                ),
                dict(
                    event="resource.waiting",
                    timestamp=at(2),
                    operation_id="run",
                    resource="compiler",
                    owner=owner,
                    held_units=4,
                    queue_depth=2,
                    older_pids=["41"],
                ),
                dict(
                    event="resource.acquired",
                    timestamp=at(4),
                    operation_id="run",
                    resource="compiler",
                    owner=owner,
                    queue_duration_ms=3000,
                ),
                dict(
                    event="ci.step_finished",
                    timestamp=at(8),
                    run_id="run",
                    span_id="child",
                    parent_span_id="run",
                    name="Compile",
                    duration_ms=4000,
                    cpu_user_ms=8000,
                    cpu_system_ms=1000,
                    cpu_scope="waited_children",
                ),
                dict(
                    event="ci.step_finished",
                    timestamp=at(9),
                    run_id="run",
                    span_id="parallel",
                    parent_span_id="run",
                    name="Worker",
                    duration_ms=4000,
                    cpu_user_ms=None,
                    cpu_system_ms=None,
                    cpu_scope=None,
                ),
                dict(
                    event="resource.released",
                    timestamp=at(9),
                    operation_id="run",
                    resource="compiler",
                    owner=owner,
                    held_duration_ms=5000,
                ),
                dict(
                    event="ci.run_finished",
                    timestamp=at(10),
                    run_id="run",
                    run_span_id="run",
                    duration_ms=10000,
                    cpu_user_ms=10000,
                    cpu_system_ms=2000,
                    cpu_scope="waited_children",
                ),
                dict(
                    code="ResourceSample",
                    schema=1,
                    project="sample",
                    host=dict(
                        start=at(5),
                        end=at(6),
                        host="host-a",
                        cpu_idle_percent=55,
                        memory_used_bytes=100,
                        swap_used_bytes=10,
                    ),
                    gate=dict(
                        start=at(6),
                        end=at(7),
                        resources=dict(queued_runs=0, cpu_reserved=0),
                        candidates=[],
                    ),
                ),
                dict(
                    code="ResourceSample",
                    schema=1,
                    project="sample",
                    host=dict(
                        start=at(5), end=at(6), host="host-b", cpu_idle_percent=99
                    ),
                    gate=dict(start=at(6), end=at(7), error="unavailable"),
                ),
            ]
            path = root / "observations.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            before = path.read_bytes()
            with patch.dict(
                os.environ,
                {
                    "HIVE_PROJECTS": json.dumps(
                        [dict(id="sample", repository=str(root))]
                    )
                },
            ):
                result = command(
                    root,
                    "telemetry",
                    "resources",
                    "--project",
                    "sample",
                    "--start",
                    at(0),
                    "--end",
                    at(11),
                    "--compare-start",
                    at(2),
                    "--compare-end",
                    at(4),
                    "--input",
                    str(path),
                    "--input",
                    str(path),
                )
            self.assertEqual(result["code"], "ResourceReport")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(objects(result["sources"])), 1)
            self.assertEqual(
                objects(result["sources"])[0]["sha256"],
                hashlib.sha256(before).hexdigest(),
            )
            window = record(result["window"])
            spans = objects(window["spans"])
            run = next(s for s in spans if s["kind"] == "ci_run")
            self.assertEqual(run["cpu_seconds"], 12)
            self.assertEqual(run["cpu_per_wall"], 1.2)
            self.assertEqual(record(run["context"])["candidate_id"], "candidate")
            self.assertEqual(record(run["context"])["head_oid"], "source")
            self.assertEqual(run["host_samples"], [0])
            child = next(s for s in spans if s["name"] == "Compile")
            self.assertEqual(child["cpu_seconds"], 9)
            self.assertNotIn("total_cpu_seconds", window)
            self.assertIsNone(
                next(s for s in spans if s["name"] == "Worker")["cpu_seconds"]
            )
            self.assertEqual(
                next(s for s in spans if s["kind"] == "nested_wait")["wall_seconds"], 3
            )
            self.assertEqual(
                record(objects(window["nested_resource_observations"])[0]["owner"]),
                owner,
            )
            self.assertEqual(
                objects(window["nested_resource_observations"])[0]["event"],
                "resource.released",
            )
            comparison = record(result["comparison"])
            # Half-open snapshot selection excludes acquisition exactly at the end.
            waiting = objects(comparison["nested_resource_observations"])[0]
            self.assertEqual(waiting["event"], "resource.waiting")
            self.assertEqual(waiting["held_units"], 4)
            self.assertEqual(waiting["older_pids"], ["41"])
            self.assertFalse(objects(comparison["spans"])[0]["fully_in_window"])
            self.assertIn(
                "host_coverage_unknown",
                sequence(record(comparison["coverage"])["gaps"], "gaps"),
            )

    def test_sampling_is_read_only_bounded_and_reports_native_gate_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake(
                root,
                {
                    "repositories": [
                        dict(
                            state=dict(id=REPO, path=str(root)),
                            resources=dict(
                                active_runs=1,
                                queued_runs=0,
                                cpu_reserved=0,
                                memory_reserved=0,
                            ),
                            queue=[
                                dict(
                                    item=dict(
                                        id="candidate",
                                        state="queued",
                                        source_oid=dict(bytes="source"),
                                        buildset_id=None,
                                    )
                                )
                            ],
                        )
                    ]
                },
            )
            with patch.dict(
                os.environ,
                {
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "HIVE_PROJECTS": json.dumps(
                        [dict(id="sample", repository=str(root))]
                    ),
                },
            ):
                sampled = command(
                    root, "telemetry", "resource-sample", "--project", "sample"
                )
            self.assertEqual(sampled["code"], "ResourceSample")
            self.assertEqual(record(sampled["gate"])["repository"], REPO)
            self.assertEqual(
                objects(record(sampled["gate"])["candidates"])[0]["id"], "candidate"
            )
            self.assertEqual(
                record(record(sampled["gate"])["resources"])["queued_runs"], 0
            )
            self.assertIn("cpu_idle_percent", record(sampled["host"]))
            self.assertEqual((root / "calls.jsonl").read_text().count("\n"), 1)
            self.assertFalse((root / "state").exists())

    def test_missing_malformed_partial_and_oversized_evidence_are_explicit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "trace.jsonl"
            path.write_text('{"event":"unrelated"}\n{"event":')
            with patch.dict(
                os.environ,
                {
                    "HIVE_PROJECTS": json.dumps(
                        [dict(id="sample", repository=str(root))]
                    )
                },
            ):
                args = (
                    "telemetry",
                    "resources",
                    "--project",
                    "sample",
                    "--start",
                    "2026-09-26T00:00:00Z",
                    "--end",
                    "2026-09-26T00:01:00Z",
                    "--input",
                    str(path),
                )
                result = command(root, *args)
                self.assertEqual(len(sequence(result["input_gaps"], "gaps")), 1)
                self.assertIn(
                    "execution_coverage_unknown",
                    sequence(
                        record(record(result["window"])["coverage"])["gaps"], "gaps"
                    ),
                )
                path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
                self.assertIn("exceeds 2 MiB", hive(root, *args).stderr)
                path.write_text("bad\n")
                self.assertIn("Malformed evidence", hive(root, *args).stderr)
                path.unlink()
                os.mkfifo(path)
                self.assertIn("regular file", hive(root, *args).stderr)

    def test_identity_and_missing_execution_are_window_specific(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "trace.jsonl"
            records: list[dict[str, object]] = [
                dict[str, object](
                    event="resource.acquired",
                    timestamp="2026-09-26T01:00:05Z",
                    operation_id="run",
                    resource=resource,
                    owner=dict[str, object](host="host", pid=pid, birth="b"),
                    queue_duration_ms=1000,
                )
                for resource, pid in (
                    ("machine-heavy", 1),
                    ("native-player", 1),
                    ("native-player", 2),
                )
            ]
            for hour, error in (("00", "unavailable"), ("01", None)):
                records.append(
                    dict[str, object](
                        code="ResourceSample",
                        schema=1,
                        project="sample",
                        host=dict[str, object](
                            start=f"2026-09-26T{hour}:00:01Z",
                            end=f"2026-09-26T{hour}:00:02Z",
                            error=error,
                        ),
                        gate=dict[str, object](
                            start=f"2026-09-26T{hour}:00:01Z",
                            end=f"2026-09-26T{hour}:00:02Z",
                            error=error,
                        ),
                    )
                )
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            with patch.dict(
                os.environ,
                {
                    "HIVE_PROJECTS": json.dumps(
                        [dict[str, object](id="sample", repository=str(root))]
                    )
                },
            ):
                result = command(
                    root,
                    "telemetry",
                    "resources",
                    "--project",
                    "sample",
                    "--start",
                    "2026-09-26T01:00:00Z",
                    "--end",
                    "2026-09-26T01:01:00Z",
                    "--compare-start",
                    "2026-09-26T00:00:00Z",
                    "--compare-end",
                    "2026-09-26T00:01:00Z",
                    "--input",
                    str(path),
                )
            current = record(result["window"])
            spans = objects(current["spans"])
            self.assertEqual(len(objects(current["nested_resource_observations"])), 3)
            self.assertEqual(len(spans), 3)
            self.assertEqual(len({str(s["id"]) for s in spans}), 3)
            gaps = sequence(record(current["coverage"])["gaps"], "gaps")
            self.assertIn("execution_coverage_unknown", gaps)
            self.assertNotIn("host_sample_unavailable", gaps)
            self.assertNotIn("gate_sample_unavailable", gaps)
            earlier = sequence(
                record(record(result["comparison"])["coverage"])["gaps"], "gaps"
            )
            self.assertIn("host_sample_unavailable", earlier)
            self.assertIn("gate_sample_unavailable", earlier)
