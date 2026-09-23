"""Observable provider outcomes and uncertainty across the subprocess boundary."""

import json
import os
import unittest
from unittest.mock import patch

from tollgate_fixture import CANDIDATE, oid, provider_fixture, reply, status, sync_event

from hive.errors import ErrorCode, HiveError
from hive.identity import SourceCommit, WorktreePath
from hive.jsonvalue import sequence
from hive.tollgate_model import CandidateState


class TollgateTests(unittest.TestCase):
    def test_delivery_wait_consumes_changes_and_requires_all_sync(self) -> None:
        with provider_fixture() as fixture:
            (fixture.repository / ".tollgate/config.toml").write_text(
                "sync_user_master = false\n"
            )
            stream = "\n".join(
                json.dumps(status(fixture.source, phase))
                for phase in ("running", "promoting", "promoted")
            )
            fixture.configure(wait={"output": stream}, history=reply([sync_event(1)]))
            result = fixture.provider.wait(CANDIDATE)
            self.assertEqual(result.state, CandidateState.PROMOTED)
            self.assertEqual(
                [call["args"] for call in fixture.calls()],
                [["wait", CANDIDATE], ["history"]],
            )
            self.assertTrue(
                all(call["cwd"] == str(fixture.repository) for call in fixture.calls())
            )
            for history, expected in [
                (
                    [sync_event(1), sync_event(2, "needs-attention")],
                    ErrorCode.SYNCHRONIZATION_REQUIRED,
                ),
                ([sync_event(2), sync_event(1, "needs-attention")], None),
                ([], ErrorCode.UNRESOLVED_OUTCOME),
                (
                    [
                        {
                            "sequence": 1,
                            "kind": "user-master.synchronized",
                            "payload": {
                                "path": str(fixture.repository),
                                "status": "updated-checkout",
                            },
                        }
                    ],
                    ErrorCode.UNRESOLVED_OUTCOME,
                ),
                (
                    [
                        sync_event(1),
                        {
                            "sequence": 2,
                            "kind": "user-master.synchronized",
                            "payload": {"snapshot_truncated": True},
                        },
                    ],
                    ErrorCode.UNRESOLVED_OUTCOME,
                ),
                (
                    [
                        {
                            "sequence": 1,
                            "kind": "user-master.synchronized",
                            "payload": {"snapshot_truncated": True},
                        },
                        sync_event(2),
                    ],
                    None,
                ),
            ]:
                with self.subTest(history=history):
                    fixture.configure(
                        wait=reply(status(fixture.source)), history=reply(history)
                    )
                    if expected is None:
                        fixture.provider.wait(CANDIDATE)
                    else:
                        with self.assertRaises(HiveError) as caught:
                            fixture.provider.wait(CANDIDATE)
                        self.assertEqual(caught.exception.code, expected)

    def test_failure_timeout_and_unknown_output_never_report_delivery(self) -> None:
        with provider_fixture() as fixture:
            for state, remote, code in [
                ("failed", "ready", ErrorCode.VALIDATION_FAILED),
                ("merge-conflict", "ready", ErrorCode.MERGE_CONFLICT),
                ("canceled", "ready", ErrorCode.CANCELLED),
                ("promoted", "abandoned", ErrorCode.SYNCHRONIZATION_REQUIRED),
                ("ready", "ready", ErrorCode.UNRESOLVED_OUTCOME),
                ("externally-integrated", "ready", ErrorCode.UNRESOLVED_OUTCOME),
            ]:
                with self.subTest(state=state):
                    fixture.configure(
                        wait=reply(status(fixture.source, state, remote), 1)
                    )
                    with self.assertRaises(HiveError) as caught:
                        fixture.provider.wait(CANDIDATE)
                    self.assertEqual(caught.exception.code, code)
            fixture.configure(wait={"output": "not-json"})
            with self.assertRaises(HiveError) as malformed:
                fixture.provider.wait(CANDIDATE)
            self.assertTrue(malformed.exception.uncertain)
            fixture.configure(wait={"output": "{}", "delay": 10})
            with self.assertRaises(HiveError) as timeout:
                fixture.provider.wait(CANDIDATE, timeout=0.2)
            self.assertEqual(timeout.exception.code, ErrorCode.DELIVERY_TIMEOUT)
            self.assertTrue(timeout.exception.uncertain)
            self.assertNotIn(
                "cancel",
                [
                    sequence(call["args"], "command arguments")[0]
                    for call in fixture.calls()
                    if isinstance(call["args"], list)
                ],
            )

    def test_submission_is_explicit_source_in_the_correct_workspace(self) -> None:
        with provider_fixture() as fixture:
            fixture.configure(
                candidate=reply(
                    {"item_id": CANDIDATE, "source_oid": oid(fixture.source)}
                )
            )
            with patch.dict(
                os.environ, {"GIT_DIR": "/invalid", "GIT_WORK_TREE": "/invalid"}
            ):
                result = fixture.provider.submit(fixture.workspace, fixture.source)
            self.assertEqual(result, CANDIDATE)
            self.assertEqual(fixture.calls()[0]["cwd"], str(fixture.workspace))
            self.assertEqual(fixture.calls()[0]["args"], ["candidate", fixture.source])
            with self.assertRaises(HiveError):
                fixture.provider.submit(
                    WorktreePath(fixture.repository), fixture.source
                )
            subdirectory = fixture.repository / "nested"
            subdirectory.mkdir()
            before = len(fixture.calls())
            with self.assertRaises(HiveError) as main_subdirectory:
                fixture.provider.submit(WorktreePath(subdirectory), fixture.source)
            self.assertEqual(main_subdirectory.exception.code, ErrorCode.INVALID_INPUT)
            self.assertEqual(len(fixture.calls()), before)
            fixture.configure(
                candidate=reply(
                    {"item_id": CANDIDATE, "source_oid": oid(SourceCommit("0" * 40))}
                )
            )
            with self.assertRaises(HiveError) as mismatch:
                fixture.provider.submit(fixture.workspace, fixture.source)
            self.assertTrue(mismatch.exception.uncertain)

    def test_authorization_checks_retained_source_and_native_acknowledgement(
        self,
    ) -> None:
        with provider_fixture() as fixture:
            fixture.configure(
                status=reply(status(fixture.source, "ready", authorized=False)),
                approve=reply(
                    {
                        "item_id": CANDIDATE,
                        "source_oid": oid(fixture.source),
                        "already_authorized": False,
                        "authorized_item_ids": [CANDIDATE],
                    }
                ),
            )
            fixture.provider.approve(CANDIDATE, fixture.source)
            with self.assertRaises(HiveError) as mismatch:
                fixture.provider.approve(CANDIDATE, SourceCommit("0" * 40))
            self.assertFalse(mismatch.exception.uncertain)
            fixture.configure(status=reply(status(fixture.source, "ready")))
            self.assertTrue(
                fixture.provider.approve(CANDIDATE, fixture.source).already_authorized
            )
            fixture.configure(
                status=reply(status(fixture.source, "ready", authorized=False)),
                approve={"output": "lost acknowledgement", "exit": 1},
            )
            with self.assertRaises(HiveError) as lost:
                fixture.provider.approve(CANDIDATE, fixture.source)
            self.assertTrue(lost.exception.uncertain)
