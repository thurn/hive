"""Pyre must reject category mistakes between real domain identifiers."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hive.jsonvalue import integer, parse, record, sequence


class IdentifierTypingTests(unittest.TestCase):
    def test_distinct_identifiers_cannot_be_interchanged(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = root / "fixture"
            fixture.mkdir()
            (root / ".pyre_configuration").write_text(
                json.dumps(
                    {
                        "source_directories": [str(source), str(fixture)],
                        "strict": True,
                        "workers": 2,
                    }
                )
            )
            (fixture / "invalid_ids.py").write_text("""
from hive.identity import BeadId, CandidateId, CodexTaskId, CodexTurnId, ProjectId
def bead(identifier: BeadId) -> None: pass
def task(identifier: CodexTaskId) -> None: pass
def candidate(identifier: CandidateId) -> None: pass
bead(CandidateId('candidate'))
task(CodexTurnId('turn'))
candidate(ProjectId('project'))
""")
            result = subprocess.run(
                [
                    str(Path(sys.executable).parent / "pyre"),
                    "--noninteractive",
                    "--output=json",
                    "check",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            errors = [record(item) for item in sequence(parse(result.stdout), "errors")]
            self.assertEqual(len(errors), 3, errors)
            self.assertEqual(
                [integer(item.get("code"), "code") for item in errors], [6] * 3
            )
