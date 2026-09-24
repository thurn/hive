"""Skill installation preflights every destination before writing."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]
ROLES = (
    "executor",
    "bead",
    "warden",
    "weaver",
    "sage",
    "vizier",
    "justiciar",
    "archivist",
    "shared",
)


class InstallSkillsTests(unittest.TestCase):
    def test_install_repeat_relative_shared_and_conflict_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "skills"
            args = [
                sys.executable,
                str(ROOT / "scripts/install-skills"),
                "--source",
                str(ROOT),
                "--dest",
                str(destination),
            ]
            for _ in range(2):
                completed = subprocess.run(
                    args, capture_output=True, text=True, timeout=10
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            for role in ROLES:
                self.assertEqual((destination / role).resolve(), ROOT / "skills" / role)
            self.assertTrue((destination / "executor/../shared/entry.md").is_file())
            (destination / "bead").unlink()
            (destination / "bead").write_text("occupied")
            (destination / "warden").unlink()
            refused = subprocess.run(args, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Conflicting skill path", refused.stderr)
            self.assertFalse((destination / "warden").exists())

    def test_claude_agent_defaults_to_claude_config_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"CLAUDE_CONFIG_DIR", "CODEX_HOME"}
            }
            environment["HOME"] = str(home)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/install-skills"),
                    "--source",
                    str(ROOT),
                    "--agent",
                    "claude",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("/executor", completed.stdout)
            for role in ROLES:
                self.assertEqual(
                    (home / ".claude/skills" / role).resolve(), ROOT / "skills" / role
                )
            self.assertFalse((home / ".codex").exists())
