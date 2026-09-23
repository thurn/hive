# Enter a Hive role

Use the canonical `~/hive/bin/hive` launcher. Examples below shorten it to
`hive`; invoke the full path unless it is already on PATH. An explicitly supplied
isolated Hive checkout/bootstrap configuration replaces that default for tests.
Do not initialize storage or replace Fulcrum skill links during ordinary work.

Read `hive config show --json` and identify the requested registered project,
repository, invariant document, and native Codex project binding. Keep execution
in that project. If the binding is missing or ambiguous, clarify before claiming;
read-only scoping can continue. `hive status --project <project> --json` supplies
existing work, owners, dependencies, capacity, and title drift. Unknown resource
metrics are not evidence of spare capacity.

Obtain the current native task ID from the invoking context (`CODEX_THREAD_ID`
when present) and inspect that task with the native `read_thread` tool. Before
owned execution, use its actual active turn ID. Never invent a turn ID or infer
activity from an emoji or timestamp. If native inspection cannot establish the
invoking turn, do not claim or edit. Filing and read-only investigation can still
proceed with a known task identity.

If the conversation already owns an in-progress bead, inspect its retained state
and call `task enter-turn` with its recorded previous turn and the actual current
turn before editing. A deferred bead needs its outstanding conditions resolved
and writers settled; merely starting another turn is not user resumption.

## Names are required UI

Record role, subject, and current bead at meaningful transitions:

```sh
hive session enter --task <native-task> --project <project> \
  --role executor --bead <bead> --subject 'Search indexing' --stage implementing \
  --json
```

Use the returned exact title with native `set_thread_title`. If renaming fails,
retry once, report drift, and continue. Record `session named --task <task>
--title <returned-title> --applied` after native success, or `--error <detail>`
after failure. Never report success just because enrollment succeeded. A stale
result returns `Busy`; inspect the current registry intent and repair that name.
Title failures do not block bead delivery.

Rename on role/bead changes, review, meaningful waits, pause, recovery, and
completion. The parent uses 🛡️ while its cold warden runs and returns to ⚒️
afterward. Omit `--bead` when leaving completed work for unrelated investigation.
Do not rename after every command. `$bead` in a reply uses `--inline-bead` and
preserves the enclosing role and title.

Enrollment and naming are the managed-UI exception for read-only specialists;
they do not authorize edits to the investigated project. Registration is not an
execution claim, and a review child shares its parent bead's slot.

## Scope and authority

Normal filed beads authorize their described implementation and ordinary
Tollgate promotion. Explicit user limits take precedence. Unapproved designs
and explicit user pauses stay deferred. Admission still controls every claim.
A specialist that chooses implementation reads the
[Hive executor skill](../executor/SKILL.md) and enters through the same admission
path. Do not import Fulcrum's coordinator,
proof, handoff, or approval machinery.
