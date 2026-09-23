"""Exercise the observable work lifecycle, not individual implementation branches."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from hive.admission import admit, next_ready, require_acyclic
from hive.errors import ErrorCode, HiveError
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
    Capacity,
    CodeDelivery,
    Deferred,
    Done,
    Drafting,
    Implementing,
    Owned,
    Owner,
    PauseReason,
    Preparing,
    Queued,
    Reviewing,
    ReviewingArtifact,
    WaitingForDelivery,
    WorkKind,
    owner_of,
)
from hive.transitions import (
    advance,
    complete,
    defer,
    enter_turn,
    recover,
    resume,
    settle,
)


def task(identifier: str, *, project: str = "search", priority: int = 2) -> Bead:
    return Bead(
        BeadId(identifier),
        ProjectId(project),
        "Repair",
        "Fix the observed failure",
        "Behavior is corrected",
        priority,
        datetime(2026, 9, 1, tzinfo=UTC),
        (),
        Queued(),
    )


def owner(number: int) -> Owner:
    return Owner(CodexTaskId(f"task-{number}"), CodexTurnId(f"turn-{number}"))


class LifecycleTests(unittest.TestCase):
    def test_each_pause_condition_requires_its_own_resolution(self) -> None:
        pending = defer(
            task("hv-plan"), PauseReason.APPROVAL, "Design pending", expected_owner=None
        )
        pending = defer(pending, PauseReason.USER, "Stop", expected_owner=None)
        pending = defer(
            pending, PauseReason.CHECKPOINT, "Resources busy", expected_owner=None
        )
        with self.assertRaises(HiveError):
            resume(pending, user_authorized=True)
        for selected in (PauseReason.APPROVAL, PauseReason.USER):
            with self.assertRaises(HiveError):
                resume(pending, reason=selected)
        pending = resume(pending, reason=PauseReason.CHECKPOINT)
        self.assertIsInstance(pending.state, Deferred)
        for first, last in (
            (PauseReason.USER, PauseReason.APPROVAL),
            (PauseReason.APPROVAL, PauseReason.USER),
        ):
            retained = resume(pending, reason=first, user_authorized=True)
            with self.assertRaises(HiveError):
                admit(retained, owner(1), retained.project, [], {}, Capacity())
            with self.assertRaises(HiveError):
                resume(retained, reason=last)
            with self.assertRaises(HiveError):
                resume(retained, reason=first, user_authorized=True)
            self.assertIsInstance(
                resume(retained, reason=last, user_authorized=True).state, Queued
            )

    def test_paused_delivery_reclaims_the_same_candidate_and_workspace(self) -> None:
        phase = WaitingForDelivery(
            WorktreePath(Path("/tmp/retained")),
            SourceCommit("a" * 40),
            CandidateId("candidate-retained"),
        )
        original = replace(task("hv-retained"), state=Owned(owner(1), phase))
        paused = settle(
            defer(original, PauseReason.USER, "Stop", expected_owner=owner(1)), owner(1)
        )
        queued = resume(paused, user_authorized=True)
        # An additional checkpoint while queued must not discard retained work.
        queued = resume(
            defer(queued, PauseReason.CHECKPOINT, "Resources busy", expected_owner=None)
        )
        reclaimed = admit(queued, owner(2), queued.project, [], {}, Capacity())
        self.assertEqual(reclaimed.state, Owned(owner(2), phase))
        delivered = complete(
            reclaimed,
            owner(2),
            "Already promoted; synchronization confirmed",
            CodeDelivery(phase.source, phase.candidate),
        )
        self.assertIsInstance(delivered.state, Done)

    def test_external_artifact_delivers_without_a_code_candidate(self) -> None:
        report = replace(task("hv-report"), kind=WorkKind.ARTIFACT)
        current = admit(report, owner(1), report.project, [], {}, Capacity())
        self.assertEqual(current.state, Owned(owner(1), Drafting()))
        with self.assertRaises(HiveError):
            advance(current, owner(1), Implementing(WorktreePath(Path("/tmp/code"))))
        current = advance(current, owner(1), ReviewingArtifact("/tmp/report.md"))
        with self.assertRaises(HiveError):
            complete(current, owner(1), "Wrong artifact", ArtifactDelivery("/other"))
        delivered = complete(
            current,
            owner(1),
            "Answers the research questions with cited evidence",
            ArtifactDelivery("/tmp/report.md"),
        )
        self.assertIsInstance(delivered.state, Done)

    def test_deliver_prerequisite_then_claim_next_work(self) -> None:
        first = task("hv-first")
        second = replace(task("hv-second", priority=0), dependencies=(first.id,))
        other = task("hv-other", project="storage", priority=0)
        all_tasks = {item.id: item for item in (first, second, other)}
        self.assertEqual(
            next_ready(first.project, list(all_tasks.values()), all_tasks), first
        )
        current = admit(
            first,
            owner(1),
            first.project,
            list(all_tasks.values()),
            all_tasks,
            Capacity(),
        )
        path = WorktreePath(Path("/tmp/workspace"))
        source = SourceCommit("a" * 40)
        candidate = CandidateId("candidate-1")
        for phase in (
            Implementing(path),
            Reviewing(path, source),
            WaitingForDelivery(path, source, candidate),
        ):
            current = advance(current, owner(1), phase)
        delivered = complete(
            current, owner(1), "Delivered the fix", CodeDelivery(source, candidate)
        )
        self.assertIsInstance(delivered.state, Done)
        all_tasks[first.id] = delivered
        self.assertEqual(next_ready(first.project, [second, other], all_tasks), second)
        self.assertIsNone(owner_of(delivered.state))

    def test_deferred_resources_retain_capacity_and_user_pause_survives(self) -> None:
        active = [
            replace(
                task(f"hv-{index}"),
                state=Owned(owner(index), Preparing(f"codex/hv-{index}")),
            )
            for index in range(8)
        ]
        paused = defer(
            active[0], PauseReason.USER, "Stop this work", expected_owner=owner(0)
        )
        for stale in (None, owner(1)):
            with self.assertRaises(HiveError) as caught:
                defer(
                    paused,
                    PauseReason.CHECKPOINT,
                    "Late callback",
                    expected_owner=stale,
                )
            self.assertEqual(caught.exception.code, ErrorCode.STALE_OWNER)
        active[0] = paused
        with self.assertRaises(HiveError) as caught:
            admit(task("hv-new"), owner(9), ProjectId("search"), active, {}, Capacity())
        self.assertEqual(caught.exception.code, ErrorCode.CAPACITY_FULL)
        with self.assertRaises(HiveError):
            resume(paused, user_authorized=True)
        with self.assertRaises(HiveError):
            recover(paused, owner(0), "old owner ended")
        settled = settle(paused, owner(0))
        self.assertIsNone(owner_of(settled.state))
        with self.assertRaises(HiveError) as caught:
            defer(
                settled,
                PauseReason.CHECKPOINT,
                "Late callback",
                expected_owner=owner(0),
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_OWNER)
        with self.assertRaises(HiveError):
            resume(settled)
        active[0] = settled
        self.assertIsInstance(
            admit(
                task("hv-new"), owner(9), ProjectId("search"), active, {}, Capacity()
            ).state,
            Owned,
        )
        self.assertIsInstance(resume(settled, user_authorized=True).state, Queued)

    def test_resumed_turn_invalidates_an_old_recovery_observation(self) -> None:
        bead = admit(task("hv-one"), owner(1), ProjectId("search"), [], {}, Capacity())
        resumed = Owner(owner(1).task, CodexTurnId("later-turn"))
        current = enter_turn(bead, owner(1), resumed)
        with self.assertRaises(HiveError) as caught:
            recover(current, owner(1), "stale observation")
        self.assertEqual(caught.exception.code, ErrorCode.STALE_OWNER)
        recovered = recover(current, resumed, "confirmed old writers stopped")
        with self.assertRaises(HiveError):
            enter_turn(recovered, resumed, owner(1))
        self.assertIsNone(owner_of(recovered.state))

    def test_direct_claim_respects_dependency_project_and_existing_ownership(
        self,
    ) -> None:
        prerequisite = replace(task("hv-prereq"), state=Cancelled("Not doing this"))
        bead = replace(task("hv-work"), dependencies=(prerequisite.id,))
        with self.assertRaises(HiveError) as caught:
            admit(
                bead,
                owner(1),
                bead.project,
                [bead],
                {prerequisite.id: prerequisite},
                Capacity(),
            )
        self.assertEqual(caught.exception.code, ErrorCode.DEPENDENCY_BLOCKED)
        with self.assertRaises(HiveError):
            admit(task("hv-work"), owner(1), ProjectId("another"), [], {}, Capacity())
        owned = admit(task("hv-owned"), owner(1), bead.project, [], {}, Capacity())
        with self.assertRaises(HiveError) as caught:
            admit(task("hv-work"), owner(1), bead.project, [owned], {}, Capacity())
        self.assertEqual(caught.exception.code, ErrorCode.ALREADY_OWNED)

    def test_per_project_ceiling_and_priority_tie_breaks(self) -> None:
        first, second = task("hv-a"), task("hv-b")
        self.assertEqual(next_ready(first.project, [second, first], {}), first)
        claimed = admit(first, owner(1), first.project, [], {}, Capacity())
        with self.assertRaises(HiveError) as caught:
            admit(
                second,
                owner(2),
                first.project,
                [claimed],
                {},
                Capacity(8, ((first.project, 1),)),
            )
        self.assertEqual(caught.exception.code, ErrorCode.CAPACITY_FULL)

    def test_wrong_candidate_or_workspace_cannot_complete_owned_code(self) -> None:
        bead = admit(task("hv-a"), owner(1), ProjectId("search"), [], {}, Capacity())
        path = WorktreePath(Path("/tmp/workspace"))
        source = SourceCommit("a" * 40)
        with self.assertRaises(HiveError):
            advance(bead, owner(1), WaitingForDelivery(path, source, CandidateId("c")))
        bead = advance(bead, owner(1), Implementing(path))
        with self.assertRaises(HiveError):
            advance(bead, owner(1), Reviewing(WorktreePath(Path("/different")), source))
        bead = advance(bead, owner(1), Reviewing(path, source))
        bead = advance(
            bead, owner(1), WaitingForDelivery(path, source, CandidateId("c"))
        )
        with self.assertRaises(HiveError):
            complete(
                bead,
                owner(1),
                "Wrong candidate",
                CodeDelivery(source, CandidateId("d")),
            )
        with self.assertRaises(HiveError):
            complete(bead, owner(1), "Still running", ArtifactDelivery("/tmp/report"))

    def test_dependency_cycle_is_rejected(self) -> None:
        a, b, c = BeadId("hv-a"), BeadId("hv-b"), BeadId("hv-c")
        with self.assertRaises(HiveError):
            require_acyclic(a, b, {b: (c,), c: (a,)})
        require_acyclic(a, b, {b: (c,)})
