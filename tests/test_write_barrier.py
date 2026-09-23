"""A dead client cannot let an outstanding server effect lose its exclusion."""

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from server_fixture import private_server

from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore, NewTask
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.jsonvalue import record
from hive.locking import Guards
from hive.write_barrier import RecordChange


class WriteBarrierTests(unittest.TestCase):
    def test_real_beads_create_with_lost_reply_retains_locatable_intent(self) -> None:
        with private_server() as (connection, _):
            root = connection.directory.parent
            guards = Guards(root / "locks")
            executable = shutil.which("bd")
            self.assertIsNotNone(executable)
            if executable is None:
                self.fail("Missing Beads executable")
            wrapper = root / "lost-reply-bd"
            wrapper.write_text(
                "#!/usr/bin/env python3\n"
                "import subprocess,sys\n"
                f"result=subprocess.run([{executable!r},*sys.argv[1:]],capture_output=True)\n"
                "if result.returncode: sys.stderr.buffer.write(result.stderr); sys.exit(result.returncode)\n"
                "sys.stdout.write('not JSON')\n"
            )
            wrapper.chmod(0o755)
            store = BeadsStore(
                BeadsProcess(connection, "lost-reply", str(wrapper)),
                guards.write_barrier,
            )
            with guards.mutation(), guards.admission():
                with self.assertRaises(HiveError) as caught:
                    store.create(
                        NewTask(ProjectId("search"), "Lost reply", "Fix", "Fixed")
                    )
            self.assertTrue(caught.exception.uncertain)
            inspection = guards.write_barrier.inspect()
            details = record(record(inspection["write"])["details"])
            nonce = details["write_nonce"]
            native = BeadsStore(BeadsProcess(connection, "inspection"))
            matches = [
                raw
                for raw in native.active_records()
                if record(record(raw.get("metadata"))["hive"]).get("write_nonce")
                == nonce
            ]
            self.assertEqual(len(matches), 1)
            self.assertTrue(str(matches[0]["id"]).startswith("hv-"))
            with self.assertRaises(HiveError) as blocked:
                with guards.admission():
                    self.fail("Lost create reply reopened admission")
            self.assertEqual(blocked.exception.code, ErrorCode.RECOVERY_REQUIRED)

    def test_dead_client_leaves_stop_while_independent_effect_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guards = Guards(Path(temporary) / "locks")
            effect = Path(temporary) / "server-effect"
            client = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    """
import subprocess, sys, time
from pathlib import Path
from hive.identity import BeadId
from hive.locking import Guards
from hive.write_barrier import RecordChange
guards = Guards(Path(sys.argv[1]))
def issue():
    subprocess.Popen(
        [sys.executable, '-c',
         'import sys,time; from pathlib import Path; time.sleep(0.5); Path(sys.argv[1]).write_text("committed")',
         sys.argv[2]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    print('issued', flush=True)
    time.sleep(30)
with guards.mutation(), guards.admission():
    guards.write_barrier.perform(RecordChange(BeadId('hv-test'), 'claim'), issue)
""",
                    str(guards.directory),
                    str(effect),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                if client.stdout is None:
                    self.fail("Missing client output")
                self.assertEqual(client.stdout.readline().strip(), "issued")
                client.kill()
                client.wait(timeout=5)
                inspection = guards.write_barrier.inspect()
                self.assertEqual(
                    inspection["write"],
                    {
                        "scope": "record",
                        "bead": "hv-test",
                        "change": "claim",
                        "details": {},
                    },
                )
                with self.assertRaises(HiveError) as caught:
                    with guards.mutation(), guards.admission():
                        self.fail("A peer entered after the client died")
                self.assertEqual(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)
                deadline = time.monotonic() + 5
                while not effect.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(effect.read_text(), "committed")
                with self.assertRaises(HiveError) as caught:
                    with guards.admission():
                        self.fail("A late server commit reopened admission")
                self.assertEqual(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)
            finally:
                if client.poll() is None:
                    client.kill()
                    client.wait(timeout=5)
                if client.stdout is not None:
                    client.stdout.close()
                if client.stderr is not None:
                    client.stderr.close()

    def test_conclusive_result_clears_marker_but_unknown_result_retains_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guards = Guards(Path(temporary))
            intent = RecordChange(BeadId("hv-test"), "pause")
            with guards.admission():
                self.assertEqual(guards.write_barrier.perform(intent, lambda: 3), 3)
            self.assertFalse(guards.write_barrier.inspect()["blocked"])

            def uncertain() -> None:
                raise HiveError(
                    ErrorCode.PROVIDER_UNAVAILABLE, "response lost", uncertain=True
                )

            with guards.admission(), self.assertRaises(HiveError):
                guards.write_barrier.perform(intent, uncertain)
            self.assertTrue(guards.write_barrier.inspect()["blocked"])
