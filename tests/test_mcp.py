"""Real stdio, CLI source selection, database routing, and process cancellation."""

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path

from cli_fixture import SCOPE, cli_fixture
from mcp_fixture import assert_stopped, await_file, mcp_client
from server_fixture import private_server
from test_source_selection import commit
from tollgate_fixture import CANDIDATE, provider_fixture, reply, status

from hive.jsonvalue import integer, parse, record, sequence, string
from hive.locking import Guards


class McpTests(unittest.TestCase):
    def test_pending_wait_keeps_protocol_live_and_next_call_uses_new_master(
        self,
    ) -> None:
        with (
            private_server() as (connection, _),
            cli_fixture(connection) as original,
            provider_fixture() as provider,
        ):
            cli = replace(
                original,
                environment={
                    **original.environment,
                    "PATH": str(provider.root)
                    + os.pathsep
                    + original.environment["PATH"],
                },
            )
            cli.call(
                "config",
                "register",
                *SCOPE,
                "--repository",
                str(provider.repository),
                "--invariants",
                str(provider.repository / "invariants.md"),
                "--native-id",
                "native-search",
            )
            with mcp_client(cli.environment) as client:
                client.send("tools/list", 0)
                self.assertIn("error", client.receive())
                client.initialize()
                client.send("tools/list", 2)
                listed = record(client.receive()["result"])
                self.assertEqual(
                    record(sequence(listed["tools"], "tools")[0])["name"],
                    "wait_for_delivery",
                )
                client.wait(3, CANDIDATE, timeout_seconds=True)
                self.assertIn("error", client.receive())
                client.write(b'{"jsonrpc":"2.0","id":true,"method":"ping"}\n')
                self.assertIn("error", client.receive())
                client.wait(30, "candidate\x00bad")
                self.assertIn("error", client.receive())
                pairs: list[dict[str, object]] = []
                for identifier in range(40, 48):
                    pairs.extend(
                        [
                            {
                                "jsonrpc": "2.0",
                                "id": identifier,
                                "method": "tools/call",
                                "params": {
                                    "name": "wait_for_delivery",
                                    "arguments": {
                                        "candidate": CANDIDATE,
                                        "project": "search",
                                    },
                                },
                            },
                            {
                                "jsonrpc": "2.0",
                                "method": "notifications/cancelled",
                                "params": {"requestId": identifier},
                            },
                        ]
                    )
                client.write(
                    ("\n".join(json.dumps(pair) for pair in pairs) + "\n").encode()
                )
                client.send("ping", 48)
                self.assertEqual(client.receive()["id"], 48)
                client.write(b"not-json\n")
                self.assertIn("error", client.receive())
                entered = provider.root / "entered"
                release = provider.root / "release"
                provider.configure(
                    locks=str(cli.state / "locks"),
                    wait={
                        **reply(status(provider.source)),
                        "release": str(release),
                        "entered": str(entered),
                    },
                    status=reply(status(provider.source)),
                )
                client.wait(4, CANDIDATE)
                await_file(entered)
                client.send("ping", 5)
                self.assertEqual(client.receive()["id"], 5)
                guards = Guards(cli.state / "locks")
                guards.stop_mutations("MCP and pending wait release maintenance")
                with guards.maintenance():
                    pass
                settings = record(
                    parse(Path(cli.environment["HIVE_BOOTSTRAP_CONFIG"]).read_text())
                )
                source = Path(string(settings["repository"], "source"))
                code = source / "src/hive/delivery_commands.py"
                code.write_text(
                    code.read_text().replace(
                        '"Delivered" if delivered',
                        '"DeliveredAfterUpdate" if delivered',
                    )
                )
                commit(source, "test: change behavior while MCP wait is pending")
                release.touch()
                first = client.receive()
                self.assertEqual(first["id"], 4)
                self.assertEqual(
                    record(record(first["result"])["structuredContent"])["code"],
                    "Delivered",
                )
                provider.configure(
                    wait=reply(status(provider.source)),
                    status=reply(status(provider.source)),
                )
                client.wait(6, CANDIDATE)
                second = record(client.receive()["result"])
                self.assertEqual(
                    record(second["structuredContent"])["code"], "DeliveredAfterUpdate"
                )
                provider.configure(
                    wait=reply(status(provider.source, "failed", "ready"), 1)
                )
                client.wait(7, CANDIDATE)
                failure = record(client.receive()["result"])
                self.assertIs(failure["isError"], True)
                self.assertEqual(
                    record(failure["structuredContent"])["code"], "ValidationFailed"
                )
                # Cancellation kills only the newly started CLI/native wait group.
                marker = provider.root / "cancel-entered"
                provider.configure(
                    wait={
                        **reply(status(provider.source)),
                        "delay": 60,
                        "ignore_term": True,
                        "entered": str(marker),
                    }
                )
                client.wait(8, CANDIDATE)
                await_file(marker)
                pid = integer(provider.calls()[-1]["pid"], "wait pid")
                client.send(
                    "notifications/cancelled",
                    requestId=8,
                    reason="User stopped waiting",
                )
                assert_stopped(pid)
                client.send("ping", 9)
                self.assertEqual(client.receive()["id"], 9)
                # EOF also drains owned wait clients, without a provider cancel.
                marker.unlink()
                client.wait(10, CANDIDATE)
                await_file(marker)
                abandoned = integer(provider.calls()[-1]["pid"], "wait pid")
                client.send("notifications/cancelled", requestId=10)
            assert_stopped(abandoned)
            self.assertNotIn(
                "cancel",
                [sequence(call["args"], "arguments")[0] for call in provider.calls()],
            )
