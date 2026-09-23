# Interruptions, failures, and retained work

Inspect the current bead before acting. Preserve workspace, source, candidate,
owner/turn, pending dependencies, and every unresolved pause condition. Unknown
outcomes stay unknown; an old timestamp or missing transcript proves nothing.

For in-scope CI failure or a merge conflict, inspect Tollgate's result and current
promoted release. Keep the same bead, repair in its retained workspace, run
checks, commit, and cold-review the changed diff before submitting a replacement.
Out-of-scope pre-existing blockers get follow-up beads and dependencies. Defer,
settle writers, and release the parent's slot before waiting on unstarted work.
An existing review or CI wait retains that slot.

A timeout/disconnection stops observation, not the candidate. Inspect the
retained candidate before reattaching a blocking wait. On lost workspace or
submission acknowledgment, use native Tollgate inventory keyed by the recorded
branch or source commit. If the outcome cannot be established, keep the last
valid phase and escalate; never blindly repeat creation or submission.

An explicit user stop adds `user-pause` promptly through `task defer`, supplying
the owning `--owner` and `--turn` pair. An old turn's callback must preserve that
pair and stop on `StaleOwner`; it must not adopt a newer turn's identity to retry.
Omitting both flags asserts that the bead is unowned, not permission to pause
whatever owner now exists. Stop your write-capable tools
and child review activity. For a retained delivery candidate, attempt supported
native Tollgate cancellation where applicable, then inspect the actual result.
Cancellation acknowledgment may be a no-op for already terminal work; it does
not prove that promotion was prevented. Use `task settle` only after your writers
have stopped. For a retained delivery phase, the command also inspects native
candidate and attempt state. Pending, unknown, or draining provider work keeps
the slot occupied; observe it with a blocking wait before retrying settlement.
An unavailable synchronization result remains unresolved, not permission to
clear ownership. Promotion already authorized may finish despite the stop. Report
what actually happened, retain the pause, and do not claim next
work. Resolving `user-pause` requires the user's explicit resumption; approval
and other outstanding conditions remain separate.

On a resumed turn, validate native identity before `task enter-turn`. That
operation cannot reopen deferred work. After authorized resumption and settled
ownership, use ordinary admission to continue the retained work. Already delivered
source needs reconciliation and the completion checklist, not implementation again.

For non-deferred work, re-entry in the same conversation preserves its candidate
and occupied slot. While the candidate is still pending, inspect or reattach the
wait; do not edit its source. Returning to implementation requires a settled
failed/cancelled candidate. Already delivered work proceeds to completion.
If source changes during the short provider inspection, the transition refuses
without writing Beads; repeat the command so it selects current Hive code.

If a previous turn left deferred ownership unsettled, the current CLI cannot
transfer that deferred owner to a new turn. Keep it visible and use justiciar
until guarded native settlement/recovery is available. Never reuse the previous
turn ID to make `settle` accept an unverified caller.

Peers may recover only after the recorded turn ended, no replacement turn or
continuation is active/queued, writers and child reviews stopped, and external
effects are reconciled. Recheck owner and turn under the normal guarded recovery
operation. Do not clear another owner with raw `bd` writes or reuse its turn ID.
If native inspection or the recovery adapter cannot establish those conditions,
leave ownership visible and use justiciar. User pauses and unexplained
interruptions are never automatic takeover permission.

Do not detach write-capable work from inspectable sessions. Retain native command
identities and process groups for settlement. If a process escaped observation,
justiciar must investigate it; a missing heartbeat is not a lease expiry.
