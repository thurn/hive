"""Check scheduling through real child processes, deadlines and interruptions."""

import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]


def worker(root: Path, name: str, result: int = 0) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import os,subprocess,sys,time; from pathlib import Path; "
        "root=Path(sys.argv[1]); name=sys.argv[2]; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "(root/name).write_text(str(os.getpid())+' '+str(child.pid)); "
        "exec('while not (root/(name+\"-release\")).exists(): time.sleep(.01)'); "
        "print(name,flush=True); sys.exit(int(sys.argv[3]))",
        str(root),
        name,
        str(result),
    ]


def scheduler_command(
    root: Path, commands: list[list[str]], seconds: float
) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import json,sys,time; from pathlib import Path; "
        "sys.path.insert(0,sys.argv[1]); from check_runner import run_checks; "
        "sys.exit(run_checks(json.loads(sys.argv[2]),Path(sys.argv[3]),"
        "time.monotonic()+float(sys.argv[4]),jobs=2))",
        str(ROOT / "scripts"),
        json.dumps(commands),
        str(root),
        str(seconds),
    ]


@contextmanager
def scheduler(
    root: Path, commands: list[list[str]], *, seconds: float = 10
) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        scheduler_command(root, commands, seconds),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


class CheckRunnerTests(unittest.TestCase):
    def started(self, root: Path, *names: str) -> None:
        deadline = time.monotonic() + 5
        while not all((root / name).exists() for name in names):
            if time.monotonic() >= deadline:
                self.fail(f"Workers did not start: {names}")
            time.sleep(0.01)

    def reaped(self, root: Path, *names: str) -> None:
        for name in names:
            for text in (root / name).read_text().split():
                pid = int(text)
                deadline = time.monotonic() + 2
                while True:
                    status = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(pid)],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if status.returncode or status.stdout.strip().startswith("Z"):
                        break
                    if time.monotonic() >= deadline:
                        self.fail(f"Worker survived cleanup: {pid} {status.stdout}")
                    time.sleep(0.01)

    def test_two_workers_overlap_without_admitting_a_third(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with scheduler(
                root, [worker(root, name) for name in ("a", "b", "c")]
            ) as run:
                self.started(root, "a", "b")
                self.assertFalse((root / "c").exists())
                (root / "a-release").touch()
                self.started(root, "c")
                for name in ("b", "c"):
                    (root / f"{name}-release").touch()
                output, _ = run.communicate(timeout=5)
                self.assertEqual(run.returncode, 0, output)
                self.assertEqual(output.count("Finished ("), 3)
                self.reaped(root, "a", "b", "c")

    def test_failure_stops_active_sibling_and_pending_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with scheduler(
                root, [worker(root, "a", 7), worker(root, "b"), worker(root, "c")]
            ) as run:
                self.started(root, "a", "b")
                (root / "a-release").touch()
                output, _ = run.communicate(timeout=5)
                self.assertEqual(run.returncode, 7, output)
                self.assertFalse((root / "c").exists())
                self.reaped(root, "a", "b")

    def test_shared_deadline_and_interrupt_clean_up_workers(self) -> None:
        for interrupt in (False, True):
            with (
                self.subTest(interrupt=interrupt),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                with scheduler(
                    root, [worker(root, "a"), worker(root, "b")], seconds=1
                ) as run:
                    self.started(root, "a", "b")
                    if interrupt:
                        run.send_signal(signal.SIGINT)
                    output, _ = run.communicate(timeout=5)
                    self.assertNotEqual(run.returncode, 0, output)
                    if not interrupt:
                        self.assertEqual(run.returncode, 124, output)
                        self.assertIn("Check deadline exceeded", output)
                    self.reaped(root, "a", "b")

    def test_outer_cancellation_drains_nested_process_groups(self) -> None:
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                inner = scheduler_command(root, [worker(root, "leaf")], 10)
                with scheduler(root, [inner]) as outer:
                    self.started(root, "leaf")
                    outer.send_signal(number)
                    output, _ = outer.communicate(timeout=5)
                    self.assertEqual(outer.returncode, 128 + number, output)
                    self.reaped(root, "leaf")

    def test_outer_cancellation_kills_resistant_nested_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resistant = worker(root, "leaf")
            resistant[2] = (
                "import signal; signal.signal(signal.SIGINT,signal.SIG_IGN); "
                + resistant[2]
            )
            inner = scheduler_command(root, [resistant], 10)
            with scheduler(root, [inner]) as outer:
                self.started(root, "leaf")
                outer.send_signal(signal.SIGTERM)
                output, _ = outer.communicate(timeout=7)
                self.assertEqual(outer.returncode, 128 + signal.SIGTERM, output)
                self.reaped(root, "leaf")
