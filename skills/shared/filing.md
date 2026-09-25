# File and maintain native beads

Use native `bd` with the [routing prefix](routing.md) on every invocation, always naming bead IDs explicitly. The examples below abbreviate that prefix. Record a useful description, acceptance criteria, priority, intended project, and `hive_origin_thread` with the actual filing thread when available. `hive_project` must be a project `id` from the bootstrap `projects`; if the intended project is unregistered, file without it and report the gap. An ordinary ready filing may include `hive_project` in flat metadata:

```sh
# Claude Code uses thread="${CLAUDE_CODE_SESSION_ID:?}" instead.
thread="${CODEX_THREAD_ID:?}" && bd --actor "$thread" create --title 'Scoped work' --description '...' --acceptance '...' --priority 2 --metadata "{\"hive_project\":\"example\",\"hive_origin_thread\":\"$thread\"}" --json
```

For prerequisites or missing approval, initially omit `hive_project`, describe the intended project and required edges, create the bead, attach edges with `bd dep add`, and inspect them. For approval hold, use native `status=deferred` without an expiring deferral date. Add `hive_project` only after edges/hold are correct, using `update <id> --set-metadata hive_project=example`. Do not select unfinished filing missing that project key, even when its ID is supplied.

`bd create --deps` and `--claim --metadata` can involve separate native writes. On uncertain replies, inspect the bead before continuing. Use native `bd dep add` and `bd dep remove` for edits, never changing another active executor's prerequisites without coordination. A new prerequisite for your own work requires checkpointing, deferral, and settlement of outstanding effects before release.

Before claiming, verify the configured project, native status, approval holds, edges and prerequisite `hive_resolution`. Open, deferred, cancelled or ambiguous prerequisites mean wait or repair. `bd ready` is a candidate list, not proof a cancelled prerequisite was completed. For work under another agent's epic, inspect the epic and its children for active assignments, dependencies, recent notes and delivery progress before selecting a child. Prefer a bounded task that can be delivered independently of active work. Reuse recorded infrastructure diagnosis and repair outcomes; coordinate unresolved overlap with the owner before starting a competing investigation or changing shared prerequisites. An unclaimed, independent child needs no additional owner or user permission.

Keep coordination in native bead notes/comments and existing parent links. After claiming a child, record this thread's ownership and bounded scope where the epic owner can discover it; update blockers with the dependency and next action, and record delivery outcomes with candidate/commit evidence and remaining acceptance. Link to the child from the epic when its coordination record would otherwise be hard to find. Update at meaningful transitions, not every tool call; do not overwrite another agent's notes or assignment. Never use implicit last-touched issue commands.

Judge capacity from active assignments, overlap, shared build queues and recent verification outcomes. Eight assigned unfinished beads is guidance, not a gate. Prefer useful independent progress; avoid adding work that mostly repeats another agent's diagnosis, contends for saturated builds or forces repeated integration checks. Reuse valid evidence for the same source while preserving required checks; do not waive validation to manufacture capacity.

For completion, settle writers and delivery, set `hive_resolution=completed`, then `bd close <id> --reason '<concise outcome>'`. Cancellation uses `hive_resolution=cancelled`, closes explicitly with the cause as `--reason`, and triggers dependent inspection. Native close retains the assignee for observation. Repair/reopen must clear historical assignee and resolution only after affected workers are coordinated.
