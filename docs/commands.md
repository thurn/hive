# Hive task commands

Run `~/hive/bin/hive` so every invocation selects committed local master.
Commands return concise text by default. Add `--json` anywhere for a structured
result; failures have a `code`, a `detail`, and an `uncertain` flag on stderr.
Successful JSON goes to stdout. A failed command exits nonzero.

`hive recovery inspect-write --json` reads a pending Beads write marker without
contacting Beads. An unresolved marker closes admission to every competing
mutation. It must not be removed merely because the old client ended or the
bead appears unchanged: an outstanding server request can still commit.
Controlled reconciliation and marker clearing are not yet implemented.

The current commands operate on Beads and local ownership. Native role acceptance,
recruitment integration, stopped-writer recovery, and cost collection are still
under implementation. These commands do not replace those
required parts of the full workflow.

For `session enter`, `session named`, and `session list`, see
[task enrollment and naming](task-ui.md). These UI operations do not claim work.

## Explicit database and project setup

Configure bootstrap routing as described in the README. Provision a distinct
server-mode Beads database with prefix `hv` first. `config initialize` creates
Hive's configuration record in that database; it does not install a service,
initialize Beads, or adopt Fulcrum's database.

```sh
hive config initialize
hive config show --json
hive config register --project search --repository /work/search \
  --invariants /work/search/INVARIANTS.md --native-id native-project-id
hive config capacity --global-limit 8 --project-limits-json '{"search":2}'
```

Capacity replaces the global limit and all per-project overrides. Registration
updates one project without overwriting concurrent registration edits: a raced
update returns `Busy`, requiring a fresh read and request. Lowering capacity
does not stop existing work. Unfinished work prevents rebinding its project to
a different repository or native project.

## Filing and claiming

Every task names its project and acceptance criteria. Filing remains available
when implementation capacity is full. Dependencies use native Beads IDs.

```sh
hive task add --project search --title 'Repair stale index' \
  --description 'Results retain deleted documents after refresh.' \
  --acceptance 'A deleted document disappears after the next refresh.' \
  --priority 1 --depends-on hv-k2m
hive task ready --project search
hive task next --project search --owner native-task-id --turn native-turn-id
hive task claim hv-fg3 --project search \
  --owner native-task-id --turn native-turn-id
```

`ready` is advisory. Both claim paths enforce project scope, dependency success,
ownership, and current capacity under the admission lock. An executor that
already owns work cannot claim a second bead. A lost response requires inspecting
that owner and task; do not blindly issue another claim.

Use `--kind artifact` for external reports. Project-file changes remain code
work even when the changed files are documentation. To file implementation that
awaits design approval, use `--defer-reason design-approval --note 'reason'`.
Normal filing authorizes implementation by default; filing does not start it.

```sh
hive task show hv-fg3
hive task priority hv-fg3 --project search --priority 0
hive task dependency hv-fg3 --project search --prerequisite hv-k2m
hive task dependency hv-fg3 --project search --prerequisite hv-k2m --remove
hive status --project search
```

Settle owned work before editing dependencies. Interrupted dependency additions
leave deferred intent; repair the graph before resuming. Cancellation does not
satisfy a dependency. Status reports corrupt work alongside healthy tasks and
uses an unknown count when corruption prevents reliable ownership accounting.
Resource metrics are currently reported as unknown until observation adapters
are implemented.

## Lifecycle and explicit pauses

The owner and native turn are required for owner-only operations. Every resumed
native turn enters before editing; stale owner or turn IDs are rejected.

```sh
hive task enter-turn hv-fg3 --project search --owner native-task-id \
  --previous-turn old-turn-id --turn new-turn-id
hive task advance hv-fg3 --project search \
  --owner native-task-id --turn new-turn-id \
  --phase-json '{"kind":"implementing","workspace":"/work/search-fg3"}'
hive task defer hv-fg3 --project search --owner native-task-id --turn new-turn-id \
  --reason user-pause --note 'User stopped'
hive task settle hv-fg3 --project search \
  --owner native-task-id --turn new-turn-id
hive task resume hv-fg3 --project search --user-authorized
```

Deferral retains ownership while writers settle. Only the owner calls `settle`
after stopping its writers; this is not peer recovery. Use `--user-authorized`
only when the user has actually resumed the work or approved the design.
For deferral, `--owner` and `--turn` together assert the expected owner. Omitting
both asserts unowned work, so a raced claim rejects that deferral. A callback
must retain its original task/turn pair; never reread a newer owner merely to
retry a stale stop. This comparison protects deferred owners as well as active
ones. Supplying only one identity field is invalid.
Each deferral reason is retained until explicitly resolved. Adding a user pause
while design approval is pending preserves both. `resume --reason user-pause
--user-authorized` resolves only the user pause; `resume --reason design-approval
--user-authorized` resolves approval. Omit `--reason` only when exactly one
condition remains. A successful resolution can still leave the bead deferred.
The last resolution returns settled work to the queue with its workspace and
candidate identity; continuation must pass admission again.

Dependency edits reject owned work, including deferred work whose writers are
settling. Checkpoint, defer, and settle before changing its prerequisites.

Phase JSON is a discriminated object. Code phases are `implementing` with a
workspace, `reviewing` with workspace and source commit, and
`waiting-for-delivery` with workspace, source, and candidate. Artifact work uses
`drafting` or `reviewing-artifact` with a location. The CLI checks fields and
valid transitions; the executor still performs review and delivery.

```sh
hive task complete hv-fg3 --project search \
  --owner native-task-id --turn native-turn-id --summary 'Delivered fix' \
  --delivery-json '{"kind":"code","source":"commit-id","candidate":"id"}'
hive task cancel hv-m8k --project search --reason 'Superseded'
```

Completion requires matching retained delivery identity and cleared external
writers. It records the executor's observed result; it does not create a second
Tollgate certification system. Artifact delivery uses
`{"kind":"artifact","location":"/work/research.md"}` instead. Cancellation
requires settled ownership and a reason. Terminal outcomes are immutable.

## Tollgate workspace and delivery

Provider mutations require the current owner/turn and appropriate phase. They
check ownership under the admission lock, then release both locks before
calling Tollgate. An acknowledgment does not update Beads implicitly: record
the returned workspace or candidate using a fresh `task advance` command.
This keeps long provider effects outside Beads transactions and source changes.

```sh
hive workspace create hv-fg3 --project search --owner task-id --turn turn-id
# Record the returned workspace as implementing, then implement and cold-review.
# Record reviewing with the committed source and workspace before submission.
hive delivery submit hv-fg3 --project search --owner task-id --turn turn-id
# Record waiting-for-delivery with that source, workspace, and candidate.
hive delivery approve hv-fg3 --project search --owner task-id --turn turn-id
hive delivery wait candidate-id --project search --timeout-seconds 3600
hive delivery inspect candidate-id --project search
```

A wait retains one native foreground client. Intermediate native status changes
do not return to the model for polling. Timeout kills the wait client only;
it does not cancel the candidate or clear bead ownership. Inspect the retained
candidate before attaching another wait. A failed CI check, conflict,
cancellation, provider outage, unresolved outcome, and incomplete synchronization
are distinct failures. Promotion alone is not delivery.

A lost workspace/submission acknowledgment leaves the earlier lifecycle phase
intact. Inspect native worktree/candidate inventory before retrying; automated
recovery inspection remains under implementation. No external command retries
mutations blindly. Complete the bead only after actual delivery and the skill's
completion checklist; then attempt the next project-scoped claim.

Delivery checks that the candidate belongs to the selected project and that its
native promoted generation's tested commit is included in local `master`.
Remote synchronization must also be complete or explicitly disabled. A local
sync opt-out is accepted only when native configuration identities establish
that it was applied to that candidate. A dirty or rewound checkout can require
local repair even after the remote push succeeds. See
[the boundary evidence](tollgate-boundary.md). Real Codex assembled-flow and
30-minute wait acceptance remain outstanding.

## Blocking tool transport

`hive mcp` exposes `wait_for_delivery` over stdio for a host that can keep a long
MCP call pending. It starts a fresh source-selecting CLI for each request;
ordinary application updates do not restart the connection or existing waits.
Cancellation stops only that request's local wait processes. See
[MCP configuration and behavior](mcp.md) for deadlines, input, and validation.
