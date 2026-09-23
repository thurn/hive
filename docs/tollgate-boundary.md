# Tollgate delivery boundary

Hive uses the installed native provider's outcomes and current Git state. It
neither reconstructs CI certificates nor requires historical events to prove a
branch's present contents. Native interfaces were inspected and exercised on
September 22–23, 2026, in disposable registered projects.

## Native operations

The adapter preserves the distinction between submitted source, tested
integration, promotion, and configured synchronization:

- `tg worktree create NAME --json` creates from promoted release and returns
  `path`, `branch`, and `new_oid`. Tollgate may remove a clean source worktree
  after promotion. Waits run from the durable project repository.
- `tg candidate HEAD --json` records source without authorizing promotion. It
  returns `item_id`, `source_oid`, and state. A conflict can produce a retained
  candidate; submission success is not delivery success.
- `tg approve ID --wait --json` authorizes that candidate. `tg wait ID --json`
  attaches to its wait. Validation-only readiness is not delivery.
- Wait output is newline-delimited JSON emitted on provider state changes.
  Hive retains one blocking subprocess; intermediate states do not return to
  the model for polling. Timeout stops this client, not provider work.
- `tg status ID --json` resolves candidate IDs globally. Hive compares its
  `item.repository_id` with the selected project's `tg status` repository ID
  before accepting inspection, settlement, approval, or successful delivery.
  Two repositories containing the same source commit are still distinct.
  Native selection can fall back to its only registration or a registered
  parent. Its returned repository path must also match the selected project
  root after canonical path resolution; an unrelated matching ID is not enough.

Primary local references are `/Users/dthurn/tollgate/README.md`, the CLI's
`apps/tg/src/main.rs`, service `crates/tollgate-service/src/lib.rs`, and domain
`crates/tollgate-domain/src/state.rs`.

## Synchronization postconditions

A successful wait must report `promoted` with remote state `synchronized` or
`disabled`. `abandoned`, `pushing`, and blocked synchronization are not success.
A fresh candidate snapshot must still identify the same source and promotion.
Its current generation must belong to that candidate.

The native generation's `tested_oid` identifies the integrated commit, which
can differ from the submitted source. Hive checks whether that commit is an
ancestor of `refs/heads/master` in the selected repository. Later commits on
master are allowed. A rewound branch or a master containing only submitted
source does not satisfy the check. Ambient Git routing and replacement-object
settings cannot substitute another repository or commit ancestry.

The installed provider synchronizes this local master ref. Its accepted
integration branch is `release`; finding the tested commit only there does not
prove local-master synchronization. A dirty checkout can leave master behind
even when the remote push has succeeded.

An explicit disabled-sync policy is the other valid outcome. Local config text
alone is insufficient: `tg config explain` must return `sync_user_master=false`
and a native configuration identity matching both the candidate generation and
the selected repository's active configuration. Hive compares these existing
native identities without computing or storing its own hashes. Missing or
mismatched policy evidence leaves the outcome unresolved.

Historical synchronization events are diagnostic only. The installed app emits
flat events without candidate identity; Hive does not correlate them by timing,
adjacency, or the last successful event. No alternate event schemas or version
fallbacks are required by the current implementation.

## Settlement and concurrency

Leaving a retained delivery phase requires inspection of its exact candidate
and source. The candidate and all reported build attempts must be terminal.
Cancellation can mark an item terminal while an attempt drains; that state
retains the bead's occupied slot. Promotion additionally requires configured
synchronization. Failed or cancelled work can settle without claiming delivery.
Unknown or missing provider fields refuse settlement.

`externally-integrated` is terminal once its attempts drain, allowing deferred
settlement. It does not authorize reimplementation or report delivery success;
the adopted-base result needs reconciliation. These provider checks do not
establish that local agent writers have stopped.

Inspection holds neither Hive lock. Before applying an observed transition,
reacquire maintenance, verify the selected source still matches local master,
and compare the observed lifecycle state under admission. Concurrent pause,
owner, and phase changes cannot be overwritten. A source change requires a
fresh invocation rather than mixing application code in a running call.

## Evidence and remaining acceptance

Portable CI exercises real subprocesses with native-shaped replies, real Git
repositories, and a real Beads server. The CLI journey includes exact owner/turn
admission, isolated workspaces, reviewed-source submission, approval, delivery,
next claiming, and lock release before provider calls. A short pending wait
survives a source update. Targeted checks cover integrated-versus-source commit
identity, later and rewound master, applied-policy mismatch, and foreign
candidates sharing the selected project's commit. Native fallback and parent
registrations cannot authorize candidates for the selected project.

The initial native smoke candidate `01a0cce5-b900-7440-8084-d81a5fae9844`
promoted source `2cb34a3fa63815e8c291abfa4e4f530e714f907a`, but the earlier
history-based adapter refused it because the emitted event lacked candidate
identity. That result motivated observing the actual branch postcondition; it
is not evidence that the current adapter remains blocked by history format.

A separate native failed-CI probe submitted source
`c447f4db8131041b7411eea63d350dc579b5c191` as candidate
`01a0cdd0-8a3f-73b0-816e-4bcf77e29d63`. Settlement returned `RecoveryRequired`
while running, then accepted the failed candidate after one blocking wait.

Native synchronization probes used a disposable project and local bare remote:

- Candidate `01a0cdde-1a6e-7363-a23d-16096f2b3403` promoted source
  `aa70339b1cba1226df741dddb691df04ca4b6f00`. With local sync enabled and remote
  disabled, Hive accepted the actual master inclusion.
- Candidate `01a0cdde-2710-7560-8027-8fc382083f3f` promoted source
  `e7834aa487801fa7d71783cbcdbafcf6cccda2d9`. Master did not contain it; Hive
  accepted the native applied disabled-sync policy instead.
- Candidate `01a0cde2-0b55-78c3-8b27-ca4d0db76331` promoted source
  `209ca6722617093a30876f5d5647d2025e8c11e8` with remote synchronization enabled.
  The deliberately dirty main checkout prevented local synchronization. Hive
  refused delivery despite remote success, then accepted it after explicit
  local fast-forward to the already certified release.

The remote-enabled probe first encountered a remote baseline mismatch because
prior promotions had remote synchronization disabled. Only the disposable bare
remote was aligned with its already-certified release before continuing the
same candidate. After restoring the dirty file, native reconcile and pull did
not repair master; explicit local repair was necessary. These observations
prove detection and recognition of repair, not automatic repair by Hive.

This is native adapter evidence, not the complete Codex/Beads/Tollgate assembled
workflow. The real 30-minute Codex wait, native stopped-writer recovery, deployed
role/hook behavior, and full failure QA remain outstanding. The disposable probe registrations were removed. No production Hive or Fulcrum
task database was created or changed for these probes.
