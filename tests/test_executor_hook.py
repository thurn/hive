"""Public hook commands against disposable native Beads and selected source."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Protocol

from server_fixture import private_server
from test_source_selection import commit, fixture

from hive.beads_connection import BeadsConnection
from hive.jsonvalue import parse, record, sequence

ROOT: Path = Path(__file__).resolve().parents[1]
THREAD = "01a0d688-285b-7990-9cc0-b828aa12bac7"
OTHER = "01a0d65f-a2a7-7220-b392-58490c20ec97"


class Invoke(Protocol):
    def __call__(
        self,
        *args: str,
        payload: dict[str, object] | None = None,
        success: bool = True,
    ) -> dict[str, object]: ...


class Native(Protocol):
    def __call__(self, *args: str) -> object: ...


class Create(Protocol):
    def __call__(self, title: str, project: str = "hive") -> str: ...


def harness(
    test: unittest.TestCase,
    tmp: str,
    connection: BeadsConnection,
    host_session: dict[str, str],
) -> tuple[
    Path,
    Path,
    dict[str, str],
    dict[str, object],
    Invoke,
    Native,
    Create,
]:
    """Selected-source repository, bootstrap settings and native Beads helpers."""
    root: Path = Path(tmp)
    environment: dict[str, str]
    repository, environment = fixture(root)
    shutil.copytree(ROOT / "src/hive", repository / "src/hive", dirs_exist_ok=True)
    shutil.copyfile(
        ROOT / "src/hive_bootstrap/settings.py",
        repository / "src/hive_bootstrap/settings.py",
    )
    commit(repository, "feat: executor check")
    config = Path(environment["HIVE_BOOTSTRAP_CONFIG"])
    settings = record(parse(config.read_text()))
    settings.update(
        beads=str(connection.directory),
        projects=[
            {
                "id": name,
                "repository": str(repository),
                "invariants": str(repository / "AGENTS.md"),
            }
            for name in ("hive", "other")
        ],
    )
    config.write_text(json.dumps(settings))
    # The test host's own session must never leak into the fixture.
    environment.pop("CODEX_THREAD_ID", None)
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    environment.update(
        **host_session,
        BEADS_DIR=str(root / "wrong"),
        BEADS_DOLT_AUTO_START="1",
        BEADS_DOLT_SERVER_DATABASE="wrong",
        GIT_DIR="/wrong",
    )

    def invoke(
        *args: str,
        payload: dict[str, object] | None = None,
        success: bool = True,
    ) -> dict[str, object]:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/hive.py"),
                "executor",
                *args,
                "--json",
            ],
            input=json.dumps(payload) if payload is not None else None,
            env=environment,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=20,
        )
        test.assertEqual(result.returncode == 0, success, result.stderr)
        return record(parse(result.stdout if success else result.stderr))

    def native(*args: str) -> object:
        result = subprocess.run(
            [
                "bd",
                "--sandbox",
                "--dolt-auto-commit",
                "off",
                "--actor",
                THREAD,
                *args,
                "--json",
            ],
            cwd=connection.directory,
            env=connection.environment(),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return parse(result.stdout)

    def create(title: str, project: str = "hive") -> str:
        result = record(
            native(
                "create",
                title,
                "--metadata",
                json.dumps({"hive_project": project}),
            )
        )
        return str(result["id"])

    return root, repository, environment, settings, invoke, native, create


class ExecutorHookTests(unittest.TestCase):
    def test_native_lifecycle_and_source_reload(self) -> None:
        connection: BeadsConnection
        environment: dict[str, str]
        with (
            private_server() as (connection, server),
            tempfile.TemporaryDirectory() as tmp,
        ):
            root, repository, environment, settings, invoke, native, create = harness(
                self, tmp, connection, {"CODEX_THREAD_ID": THREAD}
            )
            payload: dict[str, object] = {
                "hook_event_name": "Stop",
                "session_id": THREAD,
                "turn_id": "01a0d688-285b-7990-9cc0-b828aa12bac8",
                "stop_hook_active": False,
                "cwd": str(root / "removed-worktree"),
            }
            owned = create("Delivered code; performance acceptance still missing")
            native("update", owned, "--claim")
            self.assertEqual(invoke("hook", payload=payload), {})  # No role inference.
            invoke("start", "--project", "hive")
            # The incident's free-form retry/timing stop cannot disarm execution.
            invoke(
                "stop",
                "--reason",
                "326s exceeds 300s; second retry failed",
                success=False,
            )
            for kind in ("blocked", "pressure"):
                rejected = invoke(
                    "stop",
                    "--kind",
                    kind,
                    "--reason",
                    "Timing miss after retries",
                    "--recovery",
                    " ",
                    success=False,
                )
                self.assertIn("justiciar", str(rejected))
            first = invoke("hook", payload=payload)
            self.assertEqual(first["decision"], "block")
            self.assertIn(owned, str(first["reason"]))
            self.assertIn("acceptance", str(first["reason"]))
            self.assertIn("justiciar", str(first["reason"]))
            self.assertIn("explicit user acceptance", str(first["reason"]))
            repeated = invoke("hook", payload=payload)
            self.assertNotIn("decision", repeated)
            self.assertIn("unresolved", str(repeated["systemMessage"]))
            # A hook-generated user prompt preserves the correction budget even
            # when the host assigns it a new turn ID and clears its active flag.
            invoke(
                "hook",
                payload={
                    **payload,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": first["reason"],
                },
            )
            self.assertIn(
                "systemMessage",
                invoke("hook", payload={**payload, "turn_id": "turn-2"}),
            )
            # Recovery can resume useful work; an unresolved external dependency
            # can be recorded explicitly without the hook mutating ownership.
            stopped = invoke(
                "stop",
                "--kind",
                "blocked",
                "--reason",
                "External host unavailable",
                "--recovery",
                "Inspected host outage; local diagnosis exhausted; "
                "writers settled; host owner must restore access; no independent work",
            )
            self.assertEqual(stopped["kind"], "blocked")
            self.assertEqual(invoke("hook", payload=payload), {})
            self.assertEqual(
                record(sequence(native("show", owned), "beads")[0])["assignee"], THREAD
            )
            invoke("start", "--project", "hive")
            self.assertIn("systemMessage", invoke("hook", payload=payload))
            invoke(
                "stop",
                "--kind",
                "pause",
                "--reason",
                "User paused measurements until machine is idle",
            )
            self.assertEqual(invoke("hook", payload=payload), {})
            native("update", owned, "--set-metadata", "hive_resolution=completed")
            native("close", owned)
            ready = create("Next eligible work")
            deferred = create("Pending approval")
            native("update", deferred, "--status", "deferred")
            competing = create("Other worker")
            native("update", competing, "--assignee", OTHER, "--status", "in_progress")
            outside = create("Other project", "other")
            invoke(
                "hook",
                payload={
                    **payload,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "$executor",
                },
            )
            scoped = invoke("start", "--project", "hive")
            self.assertFalse(scoped["continuous"])
            self.assertEqual(invoke("hook", payload=payload), {})
            # Scope survives process boundaries and does not silently inherit a
            # previous queue opt-in after new user input.
            continuous = invoke("start", "--project", "hive", "--continuous")
            self.assertTrue(continuous["continuous"])
            second = invoke("hook", payload={**payload, "turn_id": "turn-3"})
            self.assertEqual(second["decision"], "block")
            self.assertIn(ready, str(second["reason"]))
            for excluded in (owned, deferred, competing, outside):
                self.assertNotIn(excluded, str(second["reason"]))
            self.assertEqual(
                record(sequence(native("show", ready), "beads")[0])["status"], "open"
            )
            # Actual new input disarms, including read-only questions and pauses.
            for event in ("UserPromptSubmit", "Interrupt"):
                invoke(
                    "hook",
                    payload={**payload, "hook_event_name": event, "prompt": "pause"},
                )
                self.assertEqual(invoke("hook", payload=payload), {})
                invoke("start", "--project", "hive")
                self.assertEqual(invoke("hook", payload=payload), {})
            # Hook identity wins over inherited CODEX_THREAD_ID.
            self.assertEqual(
                invoke("hook", payload={**payload, "session_id": OTHER}), {}
            )
            native("close", ready)
            self.assertEqual(invoke("hook", payload=payload), {})
            invoke(
                "stop",
                "--kind",
                "drained",
                "--reason",
                "No unfinished assignments or eligible ready work",
            )
            self.assertEqual(invoke("hook", payload=payload), {})
            invoke("start", "--project", "hive")
            native("update", ready, "--status", "open")
            invoke("start", "--project", "hive", "--continuous")
            # New source takes effect without installing or restarting a hook.
            path = repository / "src/hive/executor_hook.py"
            path.write_text(
                path.read_text().replace(
                    "Hive executor still has unresolved work.",
                    "Updated source warning.",
                )
            )
            commit(repository, "feat: update hook warning")
            self.assertIn(
                "Updated source warning.",
                str(invoke("hook", payload={**payload, "stop_hook_active": True})),
            )
            # New input must win while native reads are still in flight.
            executable = shutil.which("bd")
            self.assertIsNotNone(executable)
            wrapper = root / "wrapper"
            wrapper.mkdir()
            marker = root / "query-started"
            (wrapper / "bd").write_text(
                "#!/bin/sh\ntouch "
                + shlex.quote(str(marker))
                + "\nsleep 0.5\nexec "
                + shlex.quote(str(executable))
                + ' "$@"\n'
            )
            (wrapper / "bd").chmod(0o700)
            environment["PATH"] = str(wrapper) + os.pathsep + environment["PATH"]
            pending = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts/hive.py"),
                    "executor",
                    "hook",
                    "--json",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                cwd=root,
                text=True,
            )
            try:
                input_pipe = pending.stdin
                if input_pipe is None:
                    self.fail("Missing hook input pipe")
                input_pipe.write(json.dumps(payload))
                input_pipe.close()
                pending.stdin = None
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                invoke("hook", payload={**payload, "hook_event_name": "Interrupt"})
                output, error = pending.communicate(timeout=10)
                self.assertEqual(pending.returncode, 0, error)
                self.assertEqual(record(parse(output)), {})
            finally:
                if pending.poll() is None:
                    pending.kill()
                pending.communicate(timeout=5)
            environment["PATH"] = environment["PATH"].split(os.pathsep, 1)[1]
            invoke("start", "--project", "hive")
            receipts = sequence(
                invoke("diagnostics", "--session", THREAD)["receipts"], "receipts"
            )
            events = [record(row) for row in receipts]
            for event, outcome in (
                ("start", "activated"),
                ("stop", "disarmed"),
                ("hook-invoked", "Stop"),
                ("Stop", "unbound"),
                ("Stop", "inactive"),
                ("Stop", "correction"),
                ("Stop", "warning"),
                ("Stop", "drained"),
                ("UserPromptSubmit", "disarmed"),
                ("UserPromptSubmit", "continuation-preserved"),
                ("Interrupt", "disarmed"),
                ("Stop", "superseded"),
            ):
                self.assertTrue(
                    any(
                        row["event"] == event and row["outcome"] == outcome
                        for row in events
                    ),
                    (event, outcome),
                )
            self.assertTrue(any(row["stop_kind"] == "blocked" for row in events))
            self.assertTrue(any(row["turn"] == payload["turn_id"] for row in events))
            self.assertGreater(len({str(row["source_commit"]) for row in events}), 1)
            self.assertNotIn("User paused measurements", json.dumps(events))
            self.assertNotIn(str(first["reason"]), json.dumps(events))
            server.terminate()
            server.wait(timeout=5)
            failed = invoke("hook", payload=payload)
            self.assertNotIn("decision", failed)
            self.assertIn("unavailable", str(failed["systemMessage"]))
            self.assertFalse((root / "wrong").exists())
            malformed = invoke("hook", payload={**payload, "session_id": "../escape"})
            self.assertIn("systemMessage", malformed)

            events = sequence(
                invoke("diagnostics", "--session", THREAD)["receipts"], "receipts"
            )
            self.assertEqual(record(events[-1])["outcome"], "unavailable")
            # The global public log bounds sessions as well as events and does
            # not retain user prompts, including while execution is inactive.
            invoke(
                "hook",
                payload={
                    **payload,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "PRIVATE-PROMPT-SENTINEL",
                },
            )
            for _ in range(70):
                self.assertEqual(invoke("hook", payload=payload), {})
            diagnostic = invoke("diagnostics")
            self.assertEqual(len(sequence(diagnostic["receipts"], "receipts")), 128)
            self.assertNotIn("PRIVATE-PROMPT-SENTINEL", json.dumps(diagnostic))
            log = Path(str(settings["state"])) / "executor-diagnostics.json"
            self.assertLess(log.stat().st_size, 262_144)
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            saved = log.with_suffix(".saved")
            log.rename(saved)
            log.mkdir()
            unavailable = invoke("hook", payload=payload)
            self.assertNotIn("decision", unavailable)
            self.assertIn("diagnostics unavailable", str(unavailable["systemMessage"]))
            log.rmdir()
            saved.rename(log)
            malformed = invoke(
                "hook", payload={"hook_event_name": [], "session_id": "SECRET"}
            )
            self.assertIn("systemMessage", malformed)
            self.assertNotIn("SECRET", json.dumps(invoke("diagnostics")))

    def test_claude_code_session_lifecycle(self) -> None:
        """Claude Code binds by its own session ID; injected prompts keep it armed."""
        with (
            private_server() as (connection, _server),
            tempfile.TemporaryDirectory() as tmp,
        ):
            _, _, environment, _, invoke, native, create = harness(
                self, tmp, connection, {"CLAUDE_CODE_SESSION_ID": THREAD}
            )
            payload: dict[str, object] = {
                "hook_event_name": "Stop",
                "session_id": THREAD,
                "prompt_id": "01a0d688-285b-7990-9cc0-b828aa12bac9",
                "transcript_path": "/tmp/transcript.jsonl",
                "stop_hook_active": False,
            }
            ready = create("Next eligible work")
            self.assertEqual(invoke("hook", payload=payload), {})
            started = invoke("start", "--project", "hive", "--continuous")
            self.assertEqual(started["session"], THREAD)
            first = invoke("hook", payload=payload)
            self.assertEqual(first["decision"], "block")
            self.assertIn(ready, str(first["reason"]))
            # One correction per execution, then a visible warning, even when a
            # later Stop reports neither an active hook nor the same prompt.
            repeated = invoke(
                "hook",
                payload={
                    **payload,
                    "prompt_id": "01a0d688-285b-7990-9cc0-b828aa12bad0",
                },
            )
            self.assertNotIn("decision", repeated)
            self.assertIn("unresolved", str(repeated["systemMessage"]))
            # Host-injected prompts are not new input and keep execution armed.
            prompt = {**payload, "hook_event_name": "UserPromptSubmit"}
            invoke("hook", payload={**prompt, "source": "system", "prompt": "go on"})
            self.assertIn("systemMessage", invoke("hook", payload=payload))
            # A person's prompt disarms; Claude Code has no interrupt hook.
            invoke("hook", payload={**prompt, "source": "user", "prompt": "pause"})
            self.assertEqual(invoke("hook", payload=payload), {})
            # An inherited Codex thread never silently claims the Claude session.
            environment["CODEX_THREAD_ID"] = OTHER
            ambiguous = invoke("start", "--project", "hive", success=False)
            self.assertIn("--session", str(ambiguous))
            invoke("start", "--project", "hive", "--continuous", "--session", THREAD)
            # New input reset the correction budget, so this is a fresh correction.
            self.assertEqual(invoke("hook", payload=payload)["decision"], "block")
            native("close", ready)
            invoke(
                "stop",
                "--kind",
                "drained",
                "--reason",
                "No eligible ready work",
                "--session",
                THREAD,
            )
            self.assertEqual(invoke("hook", payload=payload), {})
            claude = record(invoke("hook-config", "--host", "claude-code")["hooks"])
            self.assertEqual(set(claude), {"Stop", "UserPromptSubmit"})
            codex = record(invoke("hook-config")["hooks"])
            self.assertEqual(set(codex), {"Stop", "UserPromptSubmit", "Interrupt"})
