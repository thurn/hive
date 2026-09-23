"""Exercise the actual supported Beads interface and retained native state."""

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from server_fixture import private_server

from hive.admission import admit
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore, NewTask
from hive.errors import HiveError
from hive.identity import BeadId, CodexTaskId, CodexTurnId, ProjectId
from hive.model import Capacity, Deferred, Owner, PauseReason, Unstarted
from hive.transitions import cancel, defer, resume, settle


class BeadsServerTests(unittest.TestCase):
    def test_native_lifecycle_preserves_other_metadata_and_survives_reconnection(
        self,
    ) -> None:
        with private_server() as (connection, _):
            store = BeadsStore(BeadsProcess(connection, "fixture"))
            original = store.create(
                NewTask(ProjectId("search"), "Repair", "Fix it", "It works")
            )
            store.process.run(
                ["update", original.id, "--metadata", '{"team":"search"}'],
                mutation=True,
            )
            owner = Owner(CodexTaskId("task-1"), CodexTurnId("turn-1"))
            claimed = admit(original, owner, original.project, (), {}, Capacity())
            store.save_lifecycle(claimed)
            self.assertEqual(store.get(original.id).state, claimed.state)
            paused = settle(defer(claimed, PauseReason.USER, "Stop"), owner)
            store.save_lifecycle(paused)
            resumed = resume(paused, user_authorized=True)
            store.save_lifecycle(resumed)
            reopened = BeadsStore(
                BeadsProcess(BeadsConnection.read(connection.directory), "new-task")
            )
            self.assertEqual(reopened.get(original.id).state, resumed.state)
            result = store.process.run(
                ["list", "--all", "--id", original.id, "--limit", "0"]
            )
            from hive.jsonvalue import record, sequence

            metadata = record(record(sequence(result, "issues")[0]).get("metadata"))
            self.assertEqual(metadata["team"], "search")
            cancelled = cancel(resumed, "Scope removed")
            store.save_lifecycle(cancelled)
            self.assertEqual(store.get(original.id).state, cancelled.state)
            self.assertEqual(store.tasks(), ())

    def test_deferred_creation_and_native_dependencies(self) -> None:
        with private_server() as (connection, _):
            store = BeadsStore(BeadsProcess(connection, "fixture"))
            first = store.create(
                NewTask(ProjectId("search"), "First", "Prerequisite", "Fixed")
            )
            second = store.create(
                NewTask(
                    ProjectId("search"),
                    "Second",
                    "Needs first",
                    "Fixed",
                    state=Deferred(PauseReason.APPROVAL, "Pending design", Unstarted()),
                )
            )
            self.assertIsInstance(second.state, Deferred)
            store.process.run(["dep", "add", second.id, first.id], mutation=True)
            self.assertEqual(store.get(second.id).dependencies, (first.id,))
            self.assertEqual(len(store.tasks()), 2)
            with self.assertRaises(HiveError) as caught:
                store.save_lifecycle(replace(first, id=BeadId("hv-missing")))
            self.assertTrue(caught.exception.uncertain)

    def test_ambient_routing_cannot_redirect_a_request(self) -> None:
        with (
            private_server() as (connection, _),
            patch.dict(
                os.environ,
                {
                    "BEADS_DIR": "/nonexistent",
                    "BEADS_DOLT_SERVER_DATABASE": "wrong",
                    "BEADS_DOLT_SERVER_PORT": "1",
                    "BD_READONLY": "true",
                },
            ),
        ):
            store = BeadsStore(BeadsProcess(connection, "fixture"))
            created = store.create(
                NewTask(ProjectId("search"), "Routed task", "Local", "Present")
            )
            self.assertEqual(store.get(created.id).title, "Routed task")

    def test_server_outage_does_not_fall_back_or_start_another_server(self) -> None:
        with private_server() as (connection, server):
            store = BeadsStore(BeadsProcess(connection, "fixture"))
            created = store.create(
                NewTask(ProjectId("search"), "Persistent", "Retained", "Present")
            )
            server.terminate()
            server.wait(timeout=5)
            with self.assertRaises(HiveError) as caught:
                BeadsStore(BeadsProcess(connection, "fixture", timeout=1)).get(
                    created.id
                )
            self.assertFalse(caught.exception.uncertain)
            self.assertTrue((connection.directory / ".beads/metadata.json").exists())

    def test_missing_or_embedded_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(HiveError):
                BeadsConnection.read(directory)
            (directory / ".beads").mkdir()
            (directory / ".beads/metadata.json").write_text(
                json.dumps({"backend": "dolt", "dolt_mode": "embedded"})
            )
            with self.assertRaises(HiveError):
                BeadsConnection.read(directory)
