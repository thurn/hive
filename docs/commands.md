# Hive task commands

Run `~/hive/bin/hive` so every invocation selects committed local master.
Commands return concise text by default. Add `--json` anywhere for a structured
result; failures have a `code`, a `detail`, and an `uncertain` flag on stderr.
Successful JSON goes to stdout. A failed command exits nonzero.

The current commands operate on Beads and local ownership. Native task naming,
recruitment, stopped-writer recovery, Tollgate integration, skills, and cost
collection are still under implementation. These commands do not replace those
required parts of the full workflow.

## Explicit database and project setup

Configure bootstrap routing as described in the README. Provision a distinct
server-mode Beads database with prefix `hv` first. `config initialize` creates
Hive's configuration record in that database; it does not install a service,
initialize Beads, or adopt Fulcrum's database.

```sh
hive config initialize
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
hive task defer hv-fg3 --project search --reason user-pause --note 'User stopped'
hive task settle hv-fg3 --project search \
  --owner native-task-id --turn new-turn-id
hive task resume hv-fg3 --project search --user-authorized
```

Deferral retains ownership while writers settle. Only the owner calls `settle`
after stopping its writers; this is not peer recovery. Use `--user-authorized`
only when the user has actually resumed the work or approved the design.
Resumption returns work to the queue with retained workspace and candidate
identity; continuation must pass admission again.

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
