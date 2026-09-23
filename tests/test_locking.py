"""Real independent processes demonstrate lock exclusion and crash release."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.locking import Guards, file_lock


class LockingTests(unittest.TestCase):
    def test_failed_maintenance_retains_a_durable_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guards = Guards(Path(temporary))
            guards.stop_mutations("conversion")
            with self.assertRaises(RuntimeError), guards.maintenance():
                raise RuntimeError("Conversion failed")
            with self.assertRaises(HiveError), guards.mutation():
                self.fail("Failed conversion reopened mutations")
            with guards.maintenance():
                pass
            with guards.mutation():
                pass

    def test_overlapping_maintenance_cannot_enter_after_its_stop_was_cleared(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guards = Guards(Path(temporary))
            guards.stop_mutations("first conversion")
            with guards.maintenance():
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        """
import sys
from pathlib import Path
from hive.errors import ErrorCode, HiveError
from hive.locking import Guards
guards = Guards(Path(sys.argv[1]))
guards.stop_mutations('second conversion')
print('stopped', flush=True)
try:
    with guards.maintenance():
        raise RuntimeError('Entered conversion without its stop')
except HiveError as error:
    if error.code != ErrorCode.INVALID_INPUT:
        raise
""",
                        temporary,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if process.stdout is None:
                    self.fail("Missing subprocess output")
                self.assertEqual(process.stdout.readline().strip(), "stopped")
            try:
                output, error = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, output + error)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
            # The second conversion never ran. Only the first succeeded.
            with guards.mutation():
                pass

    def test_process_death_releases_lock_and_mutations_respect_maintenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.lock"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys,time; from pathlib import Path; "
                        "from hive.locking import file_lock; "
                        "\nwith file_lock(Path(sys.argv[1])): "
                        "print('held',flush=True); time.sleep(30)"
                    ),
                    str(path),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertIsNotNone(process.stdout)
                if process.stdout is None:
                    self.fail("Missing subprocess output")
                self.assertEqual(process.stdout.readline().strip(), "held")
                inode = path.stat().st_ino
                with (
                    self.assertRaises(HiveError) as caught,
                    file_lock(path, timeout=0.02),
                ):
                    self.fail("A second process acquired an exclusive lock")
                self.assertEqual(caught.exception.code, ErrorCode.BUSY)
                process.kill()
                process.wait(timeout=5)
                with file_lock(path):
                    self.assertEqual(path.stat().st_ino, inode)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
            guards = Guards(Path(temporary))
            with guards.mutation():
                with guards.admission():
                    pass
            guards.stop_mutations("convert")
            with self.assertRaises(HiveError), guards.mutation():
                self.fail("Mutation passed the maintenance stop")
            with guards.maintenance():
                pass
            with guards.mutation():
                self.assertFalse(guards.stop.exists())

    def test_shared_guard_allows_concurrent_readers_but_excludes_conversion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "maintenance.lock"
            with file_lock(path, shared=True), file_lock(path, shared=True):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys; from pathlib import Path; "
                            "from hive.locking import file_lock; "
                            "from hive.errors import HiveError; "
                            "\ntry:\n with file_lock(Path(sys.argv[1]),timeout=0.03): pass"
                            "\nexcept HiveError: sys.exit(7)"
                        ),
                        str(path),
                    ],
                    env=os.environ.copy(),
                    capture_output=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 7, result.stderr.decode())
