# File and maintain native beads

Use the configured `~/hive/bin/hive-bd`, always naming bead IDs explicitly. Search for duplicates. Record a useful description, acceptance criteria, priority, intended project, and `hive_origin_thread` with the actual filing thread when available. An ordinary ready filing may include `hive_project` in flat metadata:

```sh
hive-bd --actor "$CODEX_THREAD_ID" create --title 'Scoped work' --description '...' --acceptance '...' --priority 2 --metadata '{"hive_project":"example","hive_origin_thread":"<actual-thread-id>"}' --json
```

For prerequisites or missing approval, initially omit `hive_project`, describe the intended project and required edges, create the bead, attach edges with `bd dep add`, and inspect them. For approval hold, use native `status=deferred` without an expiring deferral date. Add `hive_project` only after edges/hold are correct, using `update <id> --set-metadata hive_project=example`. Do not select unfinished filing missing that project key, even when its ID is supplied.

`bd create --deps` and `--claim --metadata` can involve separate native writes. On uncertain replies, inspect the bead before continuing. Use native `bd dep add` and `bd dep remove` for edits, never changing another active executor's prerequisites without coordination. A new prerequisite for your own work requires checkpointing, deferral, and settlement of outstanding effects before release.

Before claiming, inspect native status, edges and prerequisite `hive_resolution`. Open, deferred, cancelled or ambiguous prerequisites mean wait or repair. `bd ready` is a candidate list, not proof a cancelled prerequisite was completed. Never use implicit last-touched issue commands.

For completion, settle writers and delivery, set `hive_resolution=completed` with a concise native outcome note, then `bd close <id>`. Cancellation uses `hive_resolution=cancelled`, closes explicitly, and triggers dependent inspection. Native close retains the assignee for observation. Repair/reopen must clear historical assignee and resolution only after affected workers are coordinated.
