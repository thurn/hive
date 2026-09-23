# File useful work

Clarify material scope before treating a request as implementation-ready. Search
current project work for duplicates and prerequisites. Reuse an existing bead
when it already captures the intent; refer to it in the outcome rather than
creating another issue for the same observation.

Split independently deliverable changes and link real dependencies. Cross-project
prerequisites are visible, but do not expand the executor's project authority.
Use P0–P4, normally P2, and explain unusual urgency in the description. Do not
invent a dependency merely to represent transient CPU pressure or speculative
file overlap.

```sh
hive task add --project <project> --title 'Repair stale results' \
  --description 'Deleted documents remain after refresh.' \
  --acceptance 'After refresh, queries cannot return deleted documents.' \
  --priority 1 --depends-on <prerequisite> --json
```

Omit `--depends-on` when unnecessary; repeat it for several prerequisites. Use
`--kind artifact` only for external reports that do not change project files.
Repository documentation is project-file work. Include design/document links
and concrete acceptance in descriptions. File unclear large work as a planning
bead, not a falsely implementation-ready feature.

Implementation awaiting design approval uses `--defer-reason design-approval
--note <what-needs-approval>`. An additional user stop is another condition;
resolving one never resolves the other. File follow-up findings by default even
at capacity; filing neither claims a slot nor starts implementation.

Native dependency edits reject work that still has an owner. If your owned bead
discovers an unstarted prerequisite, checkpoint and defer it with your current
`--owner` and `--turn`, settle its writers,
then file/link the prerequisite before reopening the parent. Use the
[interruption and repair guidance](repair.md) for a crash or uncertain outcome.

A successful filing returns native `hv-` IDs. Use those IDs as returned, including
longer allocations; do not invent identifiers or claim unacknowledged creation.
If `uncertain` is true, inspect existing records before retrying creation.
