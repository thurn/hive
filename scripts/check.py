"""Run the same bounded checks locally, in Tollgate, and in GitHub CI."""

from __future__ import annotations

import ast
import os
import platform
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
    required_python = (ROOT / ".python-version").read_text().strip()
    if platform.python_version() != required_python:
        print(
            f"Checks require Python {required_python}; run scripts/prepare-check.",
            file=sys.stderr,
        )
        return 1
    # Both gates use the same defaults, regardless of the launching host/session.
    # Tests for other timezones must configure their own disposable processes.
    os.environ.update(TZ="UTC", LC_ALL="C", PYTHONUTF8="1", PYTHONHASHSEED="0")
    time.tzset()
    print(f"Check environment: Python {required_python}, TZ=UTC, LC_ALL=C", flush=True)
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    os.environ["PATH"] = os.pathsep.join(
        (
            str(Path(sys.executable).parent),
            str(ROOT / ".test-tools/bin"),
            os.environ.get("PATH", ""),
        )
    )
    fast = "--fast" in sys.argv[1:]
    # Allow the expanded process-level suites to finish on a busy development host.
    deadline = time.monotonic() + (60 if fast else 480)
    if boundary_rules():
        return 1
    python = sys.executable
    commands = [
        ["npm", "run", "check", "--prefix", "dashboard"],
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
            *(["-k", "sweep_uses_links"] if fast else []),
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
            "test_resource_report.py",
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
            "test_dashboard_api.py",
            "-v",
            *(["-k", "excerpts"] if fast else []),
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
    commands.append(
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_dashboard_server.py",
            "-v",
            *(["-k", "environment_failure"] if fast else []),
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
                "test_dashboard_budget.py",
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
                "test_tollgate*.py",
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
                "test_diagnostics.py",
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
                "test_project_observation.py",
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
                "test_executor_hook.py",
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
                "test_tool_allocation.py",
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
