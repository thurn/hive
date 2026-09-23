# Enrolled conversations and task names

A Hive conversation has one permanent Beads infrastructure record keyed by its
native Codex task ID. This registry records project, current role, title intent,
and the last reported naming outcome. It is excluded from ready work and
capacity; it is not another ownership ledger. The bead's assignee remains the
execution authority.

On role invocation and at meaningful transitions, record the current focus:

```sh
hive session enter --project search --role executor \
  --bead hv-fg3 --subject 'Search indexing' --stage 'implementing' --json
```

`--task` defaults to `CODEX_THREAD_ID`; pass the actual native identity explicitly
when it is unavailable in the command environment. Never generate a replacement
ID. Role skills must validate the invoking task against native inspection before
owned execution. Enrollment does not establish activity or authorize a claim.

The result contains the exact desired `title` and `rename_required`. Use Codex's
native `set_thread_title` tool to apply that title. Allow one retry on failure,
then record the outcome and continue useful work:

```sh
hive session named --title '⚒️ [hv-fg3] Search indexing · implementing' --applied
hive session named --title '⚒️ [hv-fg3] Search indexing · implementing' \
  --error 'Native naming request failed after one retry'
hive session list --project search
hive status --project search
```

These are alternative success/failure commands. Report `--applied` only after
native success. The CLI records the outcome; it does not itself invoke the
native tool, enqueue renames, or start a worker. Native RPC latency stays outside
local task-operation latency and both Hive locks. Full role-skill integration
and native UI acceptance remain separate work.

`status` includes desired names and pending/failed outcomes. The next meaningful
transition returns a failed name for retry. Matching already-applied intent
avoids unnecessary renames. A result for an obsolete title is refused and marks
the current intent pending: an old RPC may have overwritten a newer name.
Correction is eventual UI repair, not an atomic transaction with Codex.

Use `warden` on the parent while its cold reviewer is running, then return to
`executor`. Change the bead on the next claim. Omit `--bead` when returning to
unassigned work so completed IDs do not linger. The eight roles use the design's
exact emojis; names are never parsed to discover enrollment or ownership.

For a `$bead` reply, use `--role bead --inline-bead`. Existing enrollment returns
its enclosing role and title unchanged. If the conversation was not enrolled,
it enrolls as the bead role. The actual filing command is separate and does not
start implementation. Enrollment is currently project-bound; explicit native
project reassignment must be implemented before reusing an enrolled conversation
across projects.

Concurrent enrollment is serialized with the short local admission lock and
recovers a lost reply by finding the same native task ID. A failure after a
native Beads write reports uncertainty; inspect the registry before retrying.
An uncertain create carries a one-use write nonce to locate its native record.
The registry adds no ownership reservation or task deduplication authority.
