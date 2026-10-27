# Wait for executor work

Run the configured Hive repository's `bin/hive executor await --session <your-own-native-session-id> --json` once, from a durable directory. Its default timeout is 3900 seconds (65 minutes). Keep that process pending until it reports work, a drained queue, an inactive session or the long timeout. Repeat only after `ExecutorAwaitTimeout`; a timeout's retained Beads error is evidence to investigate, not an instruction to shorten the next wait.

When the user limits work to one epic within a project, add `--parent <epic-id>`
and, where authorized, repeated `--include-label <label>` flags (for example
`--include-label ci-fix --include-label velocity`). The parent selects direct
children; included labels add shared duties with OR semantics, always inside
the session's bound project and stream. Without these options the full stream
remains the default. The filter applies to ready and waiting work, never hides
the session's own unfinished assignments, and retains project-wide held peers
so prerequisites in another stream can keep the wait pending. The response
reports the applied scope. These options restrict one wait, not the session
binding or native claim authority; verify scope and prerequisites before claiming.
Use the authorized scope rather than repeatedly waking on unrelated ready epics.

This is an idle infrastructure wait. Give one update when entering it, then stay silent while nothing changes. General advice to keep active-work updates or individual tool waits within 60 seconds does not require shortening the native wait, waking the model every minute or sending unchanged-state commentary. The tool runtime can collect process output while the model remains suspended. Do not issue `bd ready`, process-state probes or standing-duty sweeps beside the pending wait; resume those checks when it returns or new user input arrives. Keep the native 30-second interval unless a controlled verification requires another value.

In Claude Code, start the command as a background shell task and end the turn; its completion notification resumes the session. In Codex, retain a foreground wait. Where `functions.exec` supports tool composition, put both the command and collection of any yielded process session in **one long-yield cell**:

```javascript
// @exec: {"yield_time_ms": 3900000, "max_output_tokens": 1200}
let result = await tools.exec_command({
  cmd: "/configured/hive/bin/hive executor await --session \"${CODEX_THREAD_ID:?}\" --json",
  workdir: "/durable/project/checkout",
  yield_time_ms: 1000,
  max_output_tokens: 1200,
});
let output = result.output;
while (result.session_id) {
  result = await tools.write_stdin({
    session_id: result.session_id,
    chars: "",
    yield_time_ms: 300000,
    max_output_tokens: 1200,
  });
  output += result.output;
}
text({ ...result, output });
```

Replace the two configured paths before calling it. The loop only collects output from the same process; it neither starts another wait nor queries Beads. The five-minute collection bound belongs to the process tool and runs inside the cell, without model turns. Do not call `yield_control()` in an ordinary idle wait, or periodically retrieve the cell with `functions.wait`. If the cell yields at its long deadline before returning terminal output, resume that same cell with a long yield once; do not launch a second native wait. Other Codex hosts should use their supported retained-process/completion mechanism with the longest supported collection wait; report a host limitation rather than silently substituting a 60-second native timeout.

New user input and interruptions disarm the activation. If the host leaves the process or cell pending, collect its `ExecutorInactive` result or cancel only the exact owned process before opting in again; do not leave duplicate waits behind. The command never claims or assigns work: multiple waiters can observe the same candidates, and Beads' atomic claim chooses the owner after the agent checks eligibility. Hive still checks Beads internally every 30 seconds, so this avoids repeated model activity rather than all infrastructure reads or all idle cost.
