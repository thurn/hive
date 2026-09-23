# Native Codex boundary evidence

Use native tools for task naming, inspection, recruitment, and archival. Hive
must not start a second server to manufacture activity observations about tasks
owned by the desktop app.

References:

- [App-server operations](https://learn.chatgpt.com/docs/app-server) describe
  native task identities, turn history, activity, naming, and descendant
  archival. A documented operation is not evidence of deployed access.
- The installed `codex app-server proxy --help` exposes a connection to an
  existing Unix socket. On September 23, 2026, `daemon version` failed because
  its default control socket did not exist. No daemon was started or restarted.
- Installed schemas were generated read-only into a temporary directory with
  `codex app-server generate-json-schema --experimental`. They are inspection
  evidence, not checked-in runtime bindings or a compatibility selector.

A native `read_thread` call in the current desktop task returned its exact task
ID, `active` runtime status, and the current `inProgress` turn ID. The response
also contained native command and child-review identities. A small requested
turn count still included substantial turn content; consumers should extract
needed fields immediately instead of forwarding the whole response.

That observation establishes a usable invoking-turn inspection boundary. It
has not established queued-continuation visibility, explicit user-stop cause,
complete command/process inventory, stopped descendants, or archival race
behavior. Missing distinctions must remain unknown. Do not enable peer takeover
or claim archival acceptance based only on `idle`, elapsed time, a transcript,
or an unavailable socket.

The enrollment/title CLI described in [task UI](task-ui.md) stores UI intent and
reported outcomes. It does not certify native activity. The actual role skills,
stop hooks, descendant inspection, and real native acceptance scenarios remain
required before Hive is complete.

## Stop and interruption contract

The [released hook reference](https://learn.chatgpt.com/docs/hooks), checked on
September 23, documents native `turn_id` on `Stop` and `Interrupt`.
`Stop` supplies `stop_hook_active`; returning a blocking decision creates a
continuation prompt. `Interrupt` concerns the interrupted main-thread turn,
cannot restart it, and permits only a one-to-three-second command deadline.

These documented inputs support retaining the original owner/turn pair for
deferral. They do not establish that this desktop installation delivers those
events. Native event capture, hook trust, deadline behavior, and the reminder
guard still need implementation and runtime verification; no hooks were
installed by this inspection.
