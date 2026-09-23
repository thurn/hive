# Blocking delivery over MCP

Run `~/hive/bin/hive mcp` as a per-session stdio MCP server. It exposes one tool,
`wait_for_delivery`, which invokes the same source-selecting CLI delivery wait
used by terminal callers. It does not claim work, authorize promotion, update
beads, or schedule agents.

The transport implements the external MCP `2025-06-18` protocol. That identifier
is required for MCP negotiation; it is not a Hive state format or compatibility
layer. Relevant protocol contracts are [stdio framing][stdio],
[initialization][lifecycle], [tools][tools], and [cancellation][cancellation].

Configure the host to allow at least 3,900 seconds for the tool call. The normal
provider deadline is one hour. The outer transport deadline adds 120 seconds
for source selection, startup, and final provider reads; it protects against a
CLI that cannot return normally. A longer requested wait also requires a longer
host deadline. The host's code-mode wrapper must keep the request pending for
that duration rather than yielding repeatedly to the model.

## Tool contract

After normal MCP initialization and tool discovery, invoke:

```json
{
  "name": "wait_for_delivery",
  "arguments": {
    "candidate": "native-tollgate-candidate-id",
    "project": "search",
    "timeout_seconds": 3600
  }
}
```

The response contains the CLI's JSON outcome in `structuredContent` and a text
content block. `isError` is true for failed commands, including failed CI,
conflict, timeout, and unresolved synchronization. Invalid protocol arguments
produce a JSON-RPC error before launching a command. Neither a completed tool
call nor a promoted candidate alone implies successful delivery.

Every request launches a new CLI from the configured Hive checkout. The CLI
selects committed local master before importing application code. Existing
waits retain their selected source and assets. The MCP process keeps only its
connection state and pending subprocess calls; a source update does not restart
it or drop the connection. Its protocol implementation stays consistent for
that session, while subsequent delivery operations use updated application code.

## Cancellation and resource ownership

Each wait runs in its own local process group containing the Hive CLI and its
native Tollgate wait client. These are read-only observers. Cancellation kills
that group and drains its output; it does not cancel the provider candidate,
stop the Tollgate service, clear ownership, or release a bead's capacity slot.

- MCP cancellation sends no response for the canceled request.
- EOF and process termination drain outstanding wait clients.
- Repeated cancellation cannot interrupt child cleanup.
- A call canceled before its coroutine starts still releases its pending slot.
- Ping and other protocol requests remain responsive during delivery waits.
- A connection accepts at most eight pending waits and queues none. Excess
  requests return `Busy`. This transport bound does not grant execution slots.
- Frames are limited to one MiB; invalid JSON cannot become application data.

After cancellation or timeout, inspect the retained candidate and reconcile its
actual outcome before deciding whether to wait again or defer the bead. Native
user-stop recording remains a separate runtime responsibility.

## Evidence and remaining acceptance

The black-box test starts the real stdio server, an isolated server-mode Beads
database, and native-shaped provider subprocesses. It demonstrates protocol
initialization, validation failures, a live ping during a pending call, lock
release, source behavior changing on the next call, delivery error propagation,
eight immediate call/cancel pairs, and child cleanup on cancellation plus EOF.
A controlled release file keeps the first provider wait pending across the
source commit, avoiding a timing-only hot-reload assertion.

The test deliberately includes a SIGTERM-ignoring wait client, malformed
arguments, and a NUL-containing identifier. These checks establish transport
behavior, not the complete native Codex workflow. The real 30-minute Codex/CI
wait, native interruption hooks, and installed Tollgate synchronization
capabilities described in [the provider boundary](tollgate-boundary.md) remain
acceptance work. No user MCP configuration is changed by these tests.

[stdio]: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
[lifecycle]: https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
[tools]: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
[cancellation]: https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/cancellation
