# Recruit an independent executor

Recruit only when there is independently deliverable work and resources make
parallel work sensible. Inspect active scopes, capacity, memory/CPU pressure,
Tollgate load, and likely conflicts. Leave slots unused when appropriate.
A speculative spare slot is not a reservation.

Use the native task-creation tool for a separate saved-project task. Resolve its
actual native project ID from project registration and native project discovery.
Request the saved project directly (`local` environment), since Tollgate creates
the implementation worktree. Do not create nested Codex and Tollgate worktrees.
Follow the tool's authorization rules; if independent creation is unavailable,
keep work queued rather than treating a lifecycle-bound review child as a peer.

Pass a concise cohesive prompt containing the explicit Hive executor skill path,
project/repository, optional suggested bead, scope, and relevant context. The new
executor claims its own bead through ordinary admission. If it was given an exact
bead and that initial claim fails, it reports and exits rather than silently
substituting work. Otherwise it drains that project's eligible backlog.

Recruited executors continue independently if their recruiter stops. Do not wait
for their implementation before proceeding with your own independent bead, and
do not reserve capacity in a second record. A raced recruit can be denied a claim
and exit normally.

If native creation is uncertain, inspect the native list for that attempted task
before retrying. If its identity cannot be established, report uncertainty and
stop recruiting. Do not create duplicate conversations in a loop or invent a
central reconciliation process.
