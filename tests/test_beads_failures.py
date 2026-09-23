"""Lost or malformed provider replies must not authorize a blind write retry."""

import tempfile
import unittest
from pathlib import Path

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.errors import HiveError


class BeadsFailureTests(unittest.TestCase):
    def test_write_uncertainty_survives_timeout_error_and_malformed_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "bd-fixture"
            connection = BeadsConnection(root, "127.0.0.1", 3307, "fixture", "root")
            for body in (
                "print('invalid JSON')",
                "import sys; sys.stdout.buffer.write(bytes([255]))",
                "import sys; sys.stderr.buffer.write(bytes([255])); sys.exit(3)",
                "raise SystemExit(3)",
                "import time; time.sleep(2)",
            ):
                executable.write_text("#!/usr/bin/env python3\n" + body + "\n")
                executable.chmod(0o755)
                process = BeadsProcess(
                    connection, "fixture", str(executable), timeout=0.1
                )
                for mutation in (False, True):
                    with (
                        self.subTest(body=body, mutation=mutation),
                        self.assertRaises(HiveError) as caught,
                    ):
                        process.run(["request"], mutation=mutation)
                    self.assertEqual(caught.exception.uncertain, mutation)

    def test_missing_executable_has_no_uncertain_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = BeadsProcess(
                BeadsConnection(root, "127.0.0.1", 3307, "fixture", "root"),
                "fixture",
                str(root / "missing"),
            )
            with self.assertRaises(HiveError) as caught:
                process.run(["request"], mutation=True)
            self.assertFalse(caught.exception.uncertain)
