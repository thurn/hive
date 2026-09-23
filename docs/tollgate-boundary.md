# Tollgate delivery boundary

The native CLI and local source were inspected on September 22, 2026. Local
source at `/Users/dthurn/tollgate` commit
`5322745efc719e87564aa30b5bab97ce184d5261` has a richer synchronization event than
the installed app actually emits. The real smoke test below establishes that
capability gap; source inspection alone did not establish installed support.

- `tg worktree create NAME --json` creates from promoted release and returns
  `path`, `branch`, and `new_oid`. Tollgate can remove a clean source worktree
  after promotion. Provider waits must run from the durable project repository.
- `tg candidate HEAD --json` records source without authorizing promotion. It
  returns `item_id`, `source_oid`, and current state. A conflict can still
  produce a retained candidate; submission success is not delivery success.
- `tg approve ID --wait --json` authorizes that exact candidate. `tg wait ID
  --json` attaches to its delivery wait. A validation-only candidate wait can
  finish at `ready`; a delivery wait must not treat that as delivered.
- Wait output is newline-delimited JSON emitted only on provider state changes.
  Each record has `item`, `repository_execution_state`, and `block_reasons`.
  The native command waits internally and reconnects after transport loss.
  Hive should retain one subprocess and consume its final result without
  returning intermediate statuses to the model for polling.
- A terminal `promoted` candidate can have remote state `abandoned`; this does
  not satisfy configured synchronization. Success requires synchronized remote
  state or an explicitly disabled remote.
- Local checkout synchronization has a separate result. The provider can
  return a promoted candidate after recording `user-master.sync-needs-attention`.
  In the inspected source, `tg history --json` exposes synchronization events
  identifying `item_id`, `tested_oid`, and `outcome.status`. Outcomes include
  `updated-checkout`, `updated-ref`, and `already-current`. Do not infer local
  synchronization merely from candidate promotion or a zero wait exit code.
- Before a retained delivery phase can release ownership, complete, or return
  to implementation, inspect its exact candidate and source through native
  status. The candidate and every reported build attempt must be terminal.
  Native cancellation can mark the item terminal while an attempt still drains;
  such a snapshot keeps the bead occupied. A promoted candidate additionally
  requires configured synchronization. A failed/cancelled candidate can settle
  without pretending it delivered. Unknown/missing provider fields refuse the
  transition. These checks do not establish that local agent writers stopped.
  Native `externally-integrated` is also terminal once its attempts drain, so
  it permits deferred settlement. It does not authorize reimplementation or
  report delivery success; that adopted-base outcome needs reconciliation.
- Provider inspection holds neither admission nor maintenance locks. Before
  applying the prepared transition, reacquire maintenance, check selected source
  still matches local master, and compare the observed bead state under admission.
  Concurrent pause/owner/phase changes are not overwritten. Source changes ask
  for a new command; no application code or state layout is mixed in one call.
- `tg status ID --json` is candidate-specific and returns `item`, generation,
  buildset, attempts, and other evidence. Repository-wide status also contains
  active/history items and events. Historical event payloads have a bounded
  snapshot budget and can be marked truncated. Missing evidence must remain
  distinguishable from a successful synchronization.

Primary local references:

- `/Users/dthurn/tollgate/README.md`
- `/Users/dthurn/tollgate/apps/tg/src/main.rs`: `wait_for_item_until`, `History`
- `/Users/dthurn/tollgate/crates/tollgate-service/src/lib.rs`:
  `ItemWaitStatus`, `complete_user_master_sync`, `RepositorySnapshot`
- `/Users/dthurn/tollgate/crates/tollgate-domain/src/state.rs`: candidate and
  remote states

Required validation includes timeout without candidate cancellation, failed CI,
conflict, failed remote and local synchronization, lost submission responses,
and a real pending wait across a Hive source update. These adapter checks still
do not replace the full real Codex/Beads/Tollgate assembled workflow or the
30-minute wait required by the design.

## Current adapter evidence and provider gap

Portable CI exercises real subprocesses with native-shaped protocol fixtures,
plus a real Beads server and source-selecting CLI. The journey checks exact
owner/turn admission, workspace creation, reviewed-source submission, approval,
blocking delivery, next claiming, and lock release before every provider call.
A pending subprocess survives a committed Hive source update while the next
command selects that update. This is a short fixture wait, not the required
30-minute real Codex acceptance.

A disposable real Tollgate project was also initialized with a voting file
check. Hive created its native worktree, submitted source
`2cb34a3fa63815e8c291abfa4e4f530e714f907a`, authorized candidate
`01a0cce5-b900-7440-8084-d81a5fae9844`, and waited for promotion. The native source
worktree was removed and local master contained the delivered file. The remote
was disabled for this disposable test. The test repository was unregistered
afterward; Fulcrum and Hive's production registrations were untouched.

**Hive did not report this smoke test as delivered.** The installed app emitted:

```json
{"kind":"user-master.synchronized",
 "payload":{"path":"<disposable-project>","status":"updated-checkout"}}
```

That event omits candidate identity. Hive returns `UnresolvedOutcome`, retaining
the candidate for inspection. It must not correlate by timing, adjacency, or an
unrelated later success. The adapter requires the explicit `item_id`/`outcome`
contract observed in current Tollgate source. Missing or truncated native
history is also unresolved.

A local `.tollgate/config.toml` edit is not evidence of the policy applied to a
candidate. Consequently, Hive does not skip synchronization verification based
on that file. Projects that disable local synchronization need an authoritative
provider acknowledgement of the exemption; that provider interface remains to
be established. Full native delivery acceptance is incomplete until these
provider capabilities are available and tested. Do not weaken the delivery
contract merely to make the installed-provider smoke test green.

## Native settlement check

On September 23, a separate disposable registered project ran a deliberately
failing check through the installed provider. Hive submitted source
`c447f4db8131041b7411eea63d350dc579b5c191` as candidate
`01a0cdd0-8a3f-73b0-816e-4bcf77e29d63`. Its first settlement inspection returned
`RecoveryRequired` while the native candidate was `running`. After one blocking
native wait returned failed CI, inspection accepted the settled `failed`
candidate with remote synchronization disabled. The project was unregistered
after the check; existing projects and databases were unchanged.

This establishes pending-versus-failed settlement against the installed
provider. The draining-cancellation race, changed-owner/phase comparisons, and
source-change refusal are covered by subprocess/real-Beads tests. It does not
close the local-sync evidence gap above or establish native agent-writer
settlement, the complete assembled workflow, or long-wait acceptance.
