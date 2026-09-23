"""Real Git commits and processes exercise source isolation across a live wait."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record
from hive.locking import Guards, file_lock

ROOT: Path = Path(__file__).resolve().parents[1]


def git(repository: Path, *arguments: str) -> str:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def commit(repository: Path, subject: str) -> str:
    git(repository, "add", ".")
    git(repository, "commit", "-m", subject)
    return git(repository, "rev-parse", "HEAD")


def fixture(root: Path) -> tuple[Path, dict[str, str]]:
    repository = root / "repository"
    (repository / "src/hive").mkdir(parents=True)
    (repository / "src/hive_bootstrap").mkdir()
    (repository / "scripts").mkdir()
    (repository / "pyproject.toml").write_text("[project]\ndependencies = []\n")
    (repository / "src/hive/__init__.py").write_text("")
    shutil.copyfile(ROOT / "scripts/entry.py", repository / "scripts/entry.py")
    (repository / "src/hive/cli.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "def main():\n"
        " if sys.argv[1:] == ['guard']:\n"
        "  print('ready',flush=True)\n  sys.stdin.readline()\n"
        " os.close(int(os.environ['HIVE_MUTATION_GUARD_FD']))\n"
        " if sys.argv[1:] == ['hold']:\n"
        "  print('ready',flush=True)\n  sys.stdin.readline()\n"
        " from hive.asset import VALUE\n"
        " from hive_bootstrap.late import VALUE as bootstrap_value\n"
        " print(json.dumps({'commit':os.environ['HIVE_SELECTED_COMMIT'],"
        "'value':VALUE,'asset':Path(__file__).with_name('asset.txt').read_text(),"
        "'bootstrap_value':bootstrap_value}))\n"
        " return 0\n"
    )
    (repository / "src/hive/asset.py").write_text("VALUE = 'old'\n")
    (repository / "src/hive/asset.txt").write_text("old asset")
    (repository / "src/hive_bootstrap/__init__.py").write_text("")
    (repository / "src/hive_bootstrap/late.py").write_text("VALUE = 'old bootstrap'\n")
    git(repository, "init", "-b", "master")
    git(repository, "config", "user.name", "Hive test")
    git(repository, "config", "user.email", "test@localhost")
    commit(repository, "feat: initial source")
    config = root / "bootstrap.json"
    config.write_text(
        json.dumps(
            {
                "repository": str(repository),
                "state": str(root / "local-state"),
                "beads": str(root / "brain/hive"),
            }
        )
    )
    return repository, {**os.environ, "HIVE_BOOTSTRAP_CONFIG": str(config)}


def call(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/hive.py"), "source", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )


class SourceSelectionTests(unittest.TestCase):
    def test_launcher_excludes_ambient_modules_and_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = fixture(root)
            runtime = root / "runtime"
            subprocess.run(
                [sys.executable, "-m", "venv", "--without-pip", str(runtime)],
                check=True,
                capture_output=True,
                timeout=20,
            )
            shadow = root / "shadow"
            shadow.mkdir()
            (shadow / "ambient_only.py").write_text("VALUE = 'ambient'\n")
            site = runtime / "lib/python3.12/site-packages"
            (site / "installed_only.py").write_text("VALUE = 'development package'\n")
            (repository / "src/hive/cli.py").write_text(
                "import importlib.util,json,os,sys\n"
                "def main():\n"
                " os.close(int(os.environ['HIVE_MUTATION_GUARD_FD']))\n"
                " print(json.dumps({'isolated':sys.flags.isolated,"
                "'no_site':sys.flags.no_site,"
                "'ambient':importlib.util.find_spec('ambient_only') is not None,"
                "'installed':importlib.util.find_spec('installed_only') is not None}))\n"
                " return 0\n"
            )
            commit(repository, "test: report selected import isolation")
            python = str(runtime / "bin/python")
            environment.update(HIVE_PYTHON=python, PYTHONPATH=str(shadow))
            for command in (
                [str(ROOT / "bin/hive")],
                [python, str(ROOT / "scripts/hive.py")],
            ):
                result = subprocess.run(
                    [*command, "source", "--json"],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    record(parse(result.stdout)),
                    {"isolated": 1, "no_site": 1, "ambient": False, "installed": False},
                )

    def test_new_commits_replace_new_calls_without_changing_live_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = fixture(root)
            initial = git(repository, "rev-parse", "HEAD")
            old = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts/hive.py"), "hold"],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                if old.stdout is None or old.stdout.readline().strip() != "ready":
                    self.fail("Old operation did not reach its wait")
                # A pure wait has released the inherited mutation guard.
                with file_lock(
                    root / "local-state/locks/maintenance.lock", timeout=0.1
                ):
                    pass
                (repository / "src/hive/asset.py").write_text("VALUE = 'new'\n")
                (repository / "src/hive/asset.txt").write_text("new asset")
                (repository / "src/hive_bootstrap/late.py").write_text(
                    "VALUE = 'new bootstrap'\n"
                )
                # Working-tree edits cannot leak into application behavior.
                unchanged = call(environment)
                self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
                self.assertEqual(record(parse(unchanged.stdout))["value"], "old")
                newer = commit(repository, "feat: new behavior")
                current = call(environment)
                self.assertEqual(current.returncode, 0, current.stderr)
                self.assertEqual(
                    record(parse(current.stdout)),
                    {
                        "commit": newer,
                        "value": "new",
                        "asset": "new asset",
                        "bootstrap_value": "new bootstrap",
                    },
                )
                # Ordinary calls never create package installations in snapshots.
                self.assertFalse(
                    list((root / "local-state/sources").rglob("pyvenv.cfg"))
                )
                output, error = old.communicate("resume\n", timeout=10)
                self.assertEqual(old.returncode, 0, error)
                self.assertEqual(
                    record(parse(output)),
                    {
                        "commit": initial,
                        "value": "old",
                        "asset": "old asset",
                        "bootstrap_value": "old bootstrap",
                    },
                )
                (repository / "src/hive/cli.py").write_text(
                    "this is not valid Python!\n"
                )
                broken = commit(repository, "feat: broken source")
                failed = call(environment)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(
                    record(parse(failed.stderr))["code"], "SourceUnavailable"
                )
                self.assertIn(broken, str(record(parse(failed.stderr))["detail"]))
                self.assertFalse((root / "local-state/sources" / broken).exists())
                self.assertTrue((root / "local-state/sources" / newer).exists())
            finally:
                if old.poll() is None:
                    old.kill()
                old.communicate(timeout=5)

    def test_competing_preparation_and_exclusive_maintenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = fixture(root)
            selected = git(repository, "rev-parse", "HEAD")
            processes: list[subprocess.Popen[str]] = []
            try:
                for _ in range(8):
                    processes.append(
                        subprocess.Popen(
                            [sys.executable, str(ROOT / "scripts/hive.py"), "source"],
                            env=environment,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                    )
                for process in processes:
                    output, error = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, error)
                    self.assertEqual(record(parse(output))["commit"], selected)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
            self.assertEqual(len(list((root / "local-state/sources").iterdir())), 1)
            guards = Guards(root / "local-state/locks")
            guards.stop_mutations("Fixture conversion")
            with guards.maintenance():
                failed = call(environment)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(record(parse(failed.stderr))["code"], "Busy")
            self.assertEqual(call(environment).returncode, 0)

    def test_selected_application_retains_guard_and_releases_on_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, environment = fixture(root)
            path = root / "local-state/locks/maintenance.lock"
            for terminate in (False, True):
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts/hive.py"), "guard"],
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    if (
                        process.stdout is None
                        or process.stdout.readline().strip() != "ready"
                    ):
                        self.fail("Selected application did not retain the guard")
                    with self.assertRaises(HiveError) as caught:
                        with file_lock(path, timeout=0.05):
                            self.fail("Maintenance entered an active mutation")
                    self.assertEqual(caught.exception.code, ErrorCode.BUSY)
                    if terminate:
                        process.kill()
                        process.communicate(timeout=5)
                    else:
                        _, error = process.communicate("release\n", timeout=5)
                        self.assertEqual(process.returncode, 0, error)
                    with file_lock(path, timeout=0.1):
                        pass
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)

    def test_actual_cli_source_command_needs_no_task_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = fixture(root)
            shutil.copytree(
                ROOT / "src/hive", repository / "src/hive", dirs_exist_ok=True
            )
            selected = commit(repository, "feat: real command boundary")
            output = call(environment)
            self.assertEqual(output.returncode, 0, output.stderr)
            value = record(parse(output.stdout))
            self.assertEqual(value["code"], "SourceSelected")
            self.assertEqual(value["commit"], selected)
