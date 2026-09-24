# File and maintain native beads

Use native `bd` with the [routing prefix](routing.md) on every invocation, always naming bead IDs explicitly. The examples below abbreviate that prefix. Search for duplicates. Record a useful description, acceptance criteria, priority, intended project, and `hive_origin_thread` with the actual filing thread when available. `hive_project` must be a project `id` from the bootstrap `projects`; if the intended project is unregistered, file without it and report the gap. An ordinary ready filing may include `hive_project` in flat metadata:

```sh
# Claude Code uses thread="${CLAUDE_CODE_SESSION_ID:?}" instead.
thread="${CODEX_THREAD_ID:?}" && bd --actor "$thread" create --title 'Scoped work' --description '...' --acceptance '...' --priority 2 --metadata "{\"hive_project\":\"example\",\"hive_origin_thread\":\"$thread\"}" --json
```

For prerequisites or missing approval, initially omit `hive_project`, describe the intended project and required edges, create the bead, attach edges with `bd dep add`, and inspect them. For approval hold, use native `status=deferred` without an expiring deferral date. Add `hive_project` only after edges/hold are correct, using `update <id> --set-metadata hive_project=example`. Do not select unfinished filing missing that project key, even when its ID is supplied.

`bd create --deps` and `--claim --metadata` can involve separate native writes. On uncertain replies, inspect the bead before continuing. Use native `bd dep add` and `bd dep remove` for edits, never changing another active executor's prerequisites without coordination. A new prerequisite for your own work requires checkpointing, deferral, and settlement of outstanding effects before release.

Before claiming, inspect native status, edges and prerequisite `hive_resolution`. Open, deferred, cancelled or ambiguous prerequisites mean wait or repair. `bd ready` is a candidate list, not proof a cancelled prerequisite was completed. Never use implicit last-touched issue commands.

For completion, settle writers and delivery, set `hive_resolution=completed`, then `bd close <id> --reason '<concise outcome>'`. Cancellation uses `hive_resolution=cancelled`, closes explicitly with the cause as `--reason`, and triggers dependent inspection. Native close retains the assignee for observation. Repair/reopen must clear historical assignee and resolution only after affected workers are coordinated.
