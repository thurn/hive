"""Native Beads records must retain ownership and dependency semantics."""

import copy
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from hive.bead_json import decode_bead, encode_lifecycle
from hive.errors import HiveError
from hive.identity import (
    BeadId,
    CandidateId,
    CodexTaskId,
    CodexTurnId,
    ProjectId,
    SourceCommit,
    WorktreePath,
)
from hive.model import (
    ArtifactDelivery,
    Bead,
    Cancelled,
    CodeDelivery,
    Deferred,
    Done,
    Drafting,
    Draining,
    Implementing,
    Owned,
    Owner,
    PauseReason,
    Preparing,
    Queued,
    Reviewing,
    ReviewingArtifact,
    Settled,
    State,
    Unstarted,
    WaitingForDelivery,
    WorkKind,
)


def native(state: State, kind: WorkKind = WorkKind.CODE) -> dict[str, object]:
    """Shape observed from bd 1.2.2 list on a disposable server database."""
    bead = Bead(
        BeadId("hv-j3h"),
        ProjectId("search"),
        "Second",
        "fixture",
        "Correct behavior",
        2,
        datetime(2026, 9, 23, 4, 40, 14, tzinfo=UTC),
        (BeadId("hv-czm"),),
        state,
        kind,
    )
    lifecycle = encode_lifecycle(bead)
    return {
        "id": bead.id,
        "title": bead.title,
        "description": bead.description,
        "acceptance_criteria": bead.acceptance,
        "priority": bead.priority,
        "created_at": "2026-09-23T04:40:14Z",
        "issue_type": "task",
        "status": lifecycle.status,
        "assignee": lifecycle.assignee,
        "metadata": lifecycle.metadata,
        "dependency_count": 1,
        "dependencies": [
            {"issue_id": bead.id, "depends_on_id": "hv-czm", "type": "blocks"}
        ],
    }


class BeadBoundaryTests(unittest.TestCase):
    def test_all_native_lifecycles_preserve_retained_resources(self) -> None:
        owner = Owner(CodexTaskId("task-1"), CodexTurnId("turn-1"))
        path = WorktreePath(Path("/tmp/retained"))
        source, candidate = SourceCommit("a" * 40), CandidateId("candidate-1")
        phases = (
            Preparing("codex/hv-j3h"),
            Implementing(path),
            Reviewing(path, source),
            WaitingForDelivery(path, source, candidate),
        )
        states: list[State] = [
            Queued(),
            Cancelled("Scope removed"),
            Done("Promoted", CodeDelivery(source, candidate)),
        ]
        for phase in phases:
            states.extend(
                [
                    Owned(owner, phase),
                    Queued(Settled(phase)),
                    Deferred(PauseReason.USER, "Stop", Draining(owner, phase)),
                    Deferred(PauseReason.RECOVERY, "Checked", Settled(phase)),
                ]
            )
        states.append(Deferred(PauseReason.APPROVAL, "Design pending", Unstarted()))
        for state in states:
            with self.subTest(state=state):
                decoded = decode_bead(native(state))
                self.assertEqual(decoded.state, state)
                self.assertEqual(decoded.dependencies, (BeadId("hv-czm"),))

    def test_artifact_records_do_not_require_a_workspace(self) -> None:
        owner = Owner(CodexTaskId("task-1"), CodexTurnId("turn-1"))
        states: tuple[State, ...] = (
            Owned(owner, Drafting()),
            Owned(owner, ReviewingArtifact("/tmp/report.md")),
            Done("Answered the questions", ArtifactDelivery("/tmp/report.md")),
        )
        for state in states:
            self.assertEqual(decode_bead(native(state, WorkKind.ARTIFACT)).state, state)
            with self.assertRaises(HiveError):
                decode_bead(native(state, WorkKind.CODE))

    def test_corrupt_ownership_and_partial_dependency_reads_fail_closed(self) -> None:
        baseline = native(Queued())
        corruptions: tuple[dict[str, object], ...] = (
            {"assignee": "unsettled-owner"},
            {"status": "in_progress"},
            {"status": "closed"},
            {"priority": True},
            {"priority": 5},
            {"dependency_count": 2},
            {"dependencies": []},
            {"id": "fulcrum-task"},
            {"created_at": "2026-09-01"},
            {"issue_type": "agent"},
            {"metadata": {"hive": "JSON-shaped string"}},
            {
                "dependencies": [
                    {
                        "issue_id": "hv-j3h",
                        "depends_on_id": "hv-czm",
                        "type": "conditional-blocks",
                    }
                ]
            },
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption), self.assertRaises(HiveError):
                decode_bead({**baseline, **corruption})

    def test_lifecycle_update_replaces_only_the_hive_metadata_subtree(self) -> None:
        data = native(Queued())
        bead = decode_bead(data)
        patch = encode_lifecycle(replace(bead, state=Cancelled("No longer required")))
        # bd --metadata performs a shallow merge, so replacing this subtree also
        # removes obsolete ownership/phase keys without erasing unrelated data.
        existing = {"team": "search", **copy.deepcopy(patch.metadata)}
        data.update(status=patch.status, assignee=patch.assignee, metadata=existing)
        self.assertEqual(decode_bead(data).state, Cancelled("No longer required"))
        self.assertEqual(existing["team"], "search")

    def test_inconsistent_phase_fields_cannot_hide_a_candidate(self) -> None:
        data = native(Queued())
        data["metadata"] = {
            "hive": {
                "project": "search",
                "kind": "code",
                "phase": {
                    "kind": "implementing",
                    "workspace": "/tmp/w",
                    "candidate": "still-running",
                },
            }
        }
        with self.assertRaises(HiveError):
            decode_bead(data)
