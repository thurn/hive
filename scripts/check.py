"""Run the same bounded checks locally, in Tollgate, and in GitHub CI."""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]


def boundary_rules() -> int:
    """Keep unchecked typing escape hatches out of application code."""
    failures: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "typing",
                "typing_extensions",
            }:
                for alias in node.names:
                    if alias.name in {"Any", "cast"}:
                        failures.append(f"{path}:{node.lineno}: {alias.name} forbidden")
            if isinstance(node, ast.Attribute) and node.attr in {"Any", "cast"}:
                failures.append(f"{path}:{node.lineno}: typing escape hatch forbidden")
        for number, line in enumerate(source.splitlines(), 1):
            if any(
                marker in line
                for marker in (
                    "type: ignore",
                    "pyre-ignore",
                    "pyre-fixme",
                    "pyre-unsafe",
                )
            ):
                failures.append(f"{path}:{number}: type suppression forbidden")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


def run(command: list[str], deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 124
    print("+ " + " ".join(command[1:]), flush=True)
    process = subprocess.Popen(command, cwd=ROOT, process_group=0)
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        print("Check deadline exceeded", file=sys.stderr)
        return 124
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main() -> int:
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    os.environ["PATH"] = os.pathsep.join(
        (
            str(Path(sys.executable).parent),
            str(ROOT / ".test-tools/bin"),
            os.environ.get("PATH", ""),
        )
    )
    fast = "--fast" in sys.argv[1:]
    deadline = time.monotonic() + (30 if fast else 180)
    if boundary_rules():
        return 1
    python = sys.executable
    commands = [
        [python, "-m", "ruff", "check", "src", "tests", "scripts"],
        [python, "-m", "black", "--check", "src", "tests", "scripts"],
        [str(Path(python).parent / "pyre"), "--noninteractive", "check"],
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_usage.py",
            "-v",
        ],
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_cost.py",
            "-v",
        ],
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_links.py",
            "-v",
        ],
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_contention.py",
            "-v",
        ],
    ]
    commands.append(
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_claude.py",
            "-v",
        ]
    )
    commands.append(
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_install_skills.py",
            "-v",
        ]
    )
    commands.append(
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_request_detail.py",
            "-v",
        ]
    )
    if not fast:
        commands.append(
            [
                python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_bead_cost.py",
                "-v",
            ]
        )
        commands.append(
            [
                python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_bead_history.py",
                "-v",
            ]
        )
        commands.append(
            [
                python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_breakdown.py",
                "-v",
            ]
        )
        commands.append(
            [
                python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_events.py",
                "-v",
            ]
        )
        commands.append(
            [
                python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_otlp.py",
                "-v",
            ]
        )
        commands.extend(
            (
                [
                    python,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_source_selection.py",
                    "-v",
                ],
                [
                    python,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_routing.py",
                    "-v",
                ],
            )
        )
    for command in commands:
        result = run(command, deadline)
        if result:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
