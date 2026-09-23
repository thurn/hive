# Implementation and acceptance status

Authoritative specification:
`/Users/dthurn/fulcrum/hive-design.md` (published in thurn/fulcrum).
The user requested the complete system, in independently passing commits.
Nothing in this tracking document reduces that scope.

## Commit-sized work

- CI foundation: reproducible install, lint, Black, strict Pyre, unit tests,
  typing boundary checks, GitHub and Tollgate CI. Implemented; local checks
  and cold review passed. Hosted CI and Tollgate publication are checked
  separately against the actual commit.
- Typed lifecycle, ownership, admission, dependencies, and cooperative locks:
  pure domain operations implemented, with process-level lock exclusion and
  crash-release tests, protected pauses, retained delivery continuation, and
  artifact completion. Deferred work retains independent release conditions;
  resolving a user pause cannot discard pending design approval. Native JSON and
  the CLI expose every condition, and ambiguous resumption is refused. Negative
  Pyre fixtures reject interchangeable IDs.
  Deferral compares the expected task/turn pair under the admission lock;
  callbacks from prior turns cannot pause a newer attempt or settled work.
  Omitting the pair explicitly expects unowned work and cannot race a claim.
  Native stopped-writer checks remain adapter work; pure recovery operations
  are not sufficient evidence for safe peer recovery.
- Server-only Beads adapter, configuration, CLI, real concurrent database tests:
  native lifecycle/dependency JSON boundaries implemented and validated against
  observed server output. See `beads-boundary.md` for atomicity findings.
  Explicit server transport and native create/read/update primitives are now
  covered by disposable-server tests, including routing isolation, outage,
  retained lifecycle, dependency hydration, and uncertain responses. CI prepares
  pinned test binaries on Linux and macOS. Guarded compound admission, filing,
  transitions, project registration, and capacity changes are implemented.
  Independent processes race real database claims, keep review within eight
  slots, and exercise cross-project dependencies and project ceilings. A killed
  filer leaves deferred dependency intent that another process can repair.
  Settled work keeps its execution binding; paused historical corruption does
  not block unrelated ready work. Source-selected task commands now expose
  filing, direct/next claims, ready/show/status reads, lifecycle changes,
  dependencies, priorities, project registration, and capacity configuration.
  Real CLI/server journeys cover protected pauses, stale turns, artifact
  completion, follow-on claims, maintenance, provider outage, malformed status,
  and compare-and-swap configuration edits. Native recovery and the complete
  Codex/Tollgate workflow remain unimplemented. The replacement design also
  requires a durable marker for uncertain admission-relevant server writes;
  existing kernel-lock crash tests do not establish that guarantee. Fencing all
  competing transitions and reconciling a request that outlives its client
  remain required.
- Local-master bootstrap and immutable source selection are implemented for
  the command entrypoint. Real Git/process tests keep a call alive across a
  commit, verify delayed imports and assets remain consistent, ignore working
  edits, reject broken new source, and race eight preparation clients. A shared
  maintenance guard is acquired before source selection and transferred to the
  selected application. The initial `source` diagnostic releases it without
  contacting Beads. Task mutations retain that guard through their
  guarded operation; read commands release it first. Exceptional dependency
  and state maintenance tooling remain; this is not the real CI-wait test.
  Normal calls now reuse one isolated interpreter for bootstrap and application.
  The selected path excludes ambient modules and editable-package hooks; delayed
  application and bootstrap imports remain on the invocation's source snapshot.
- Tollgate workspace creation, reviewed-source submission, exact candidate
  approval, inspection, and foreground blocking waits are implemented. Owner
  checks precede external effects; neither Hive lock spans provider calls.
  Subprocess tests and a real-Beads CLI journey cover failure/uncertainty,
  synchronization, stale owners, retained capacity, next claims, and a source
  update during a pending short wait. Native delivery now checks the selected
  repository and actual inclusion of the promoted tested commit in local master,
  with an opt-out tied to the provider's applied candidate configuration. Real
  disposable native probes cover enabled/disabled sync and a dirty checkout
  despite successful remote synchronization; explicit local repair is recognized.
  See `tollgate-boundary.md`. Full assembled delivery acceptance and native
  recovery inspection remain. A per-session stdio MCP transport
  now launches a fresh source-selecting CLI for each blocking wait. The real
  transport/server fixture covers hot reload during a pending call, ordinary
  and pre-start cancellation, repeated cancellation followed by EOF, and
  draining native wait clients without canceling provider work. The native
  Codex 30-minute call and full assembled acceptance remain unproven.
  Leaving a retained delivery phase now checks candidate and build-attempt
  settlement outside both Hive locks. Pending delivery cannot return to editing,
  complete, or release deferred capacity; a terminal cancelled item still
  retains its slot while a native attempt drains. A guarded state comparison
  preserves concurrent pauses and ownership changes. A source change during
  inspection refuses the mutation until a fresh invocation. Real Beads/CLI
  tests exercise these transitions and races; native stopped-writer recovery
  remains a separate gap.
- Native task enrollment and typed title intent/outcome commands are implemented
  in Beads infrastructure records. CLI/server tests race enrollment, preserve an
  enclosing role during inline filing, retain capacity independence, and repair
  stale title acknowledgements. Status exposes drift without blocking work.
  See `task-ui.md` and `codex-boundary.md`. Actual native role integration,
  recruitment, and deployed interruption/stop event verification remain.
- All eight role skills and shared entry, filing, delivery, interruption and
  recruitment instructions are authored under `skills`. `config show` exposes
  read-only project bindings for role entry. These instructions use implemented
  task/UI/delivery commands and native tools; they are not installed over
  Fulcrum's live names. Real role behavior, native hook acceptance, emergency control
  ergonomics, archival history/exemptions, and native acceptance remain.
  Archivist explicitly refuses unsafe cleanup until its missing boundary exists.
  The eight skill manifests and local links validate. Independent read-only
  forward evaluation covers full capacity, planning approval, inline filing,
  active/unknown archival candidates, and overlapping pause conditions. This is
  instruction evidence, not the still-required native assembled workflow.
- Native-shaped Stop/Interrupt handling now enters through the source-selected
  launcher. A local atomic guard limits reminders per turn and completion;
  Beads admission predicates decide whether a reminder is useful. Interruptions
  retain their native owner pair, pause the exact claim without releasing its
  slot, and can be reapplied at turn entry after a temporary Beads failure.
  See `hooks.md`. Hook trust/configuration, actual deployed event delivery,
  deadline acceptance, and ended-turn pruning remain; these are not established
  by protocol fixtures or the pure guard's cleanup operation.
- Archivist eligibility, descendant checks, race repair, manual unarchive.
- Incremental native usage ingestion now records bounded transcript progress
  and per-response usage in one derived SQLite transaction. Fresh command and
  independent-process tests cover restarts, archive moves, partial records,
  missing data, conflicting responses, bounded oversized-record skipping, and
  collector/database failures without affecting task execution. See
  `usage-collection.md`. A separate resident now launches bounded fresh-source
  sweeps over enrolled tasks, using a narrow read-only native transcript index.
  Real CLI/server tests cover fair progress, archive-path discovery, provider
  outages with cached observations, source replacement without timer restart,
  and stopping a blocked observation child. Host-service installation, native
  response-start attribution, actual upstream model/tier coverage, trace spans,
  and retention remain required. `cost --task` now reports API-equivalent
  estimates from configured turn models and explicit tier assumptions, retains
  exact historical rate evidence, and exposes unpriced usage and source gaps.
  See `cost-reporting.md`. Bead/project/role attribution and aggregation remain;
  a turn's configured model is not proof of each upstream response's settings.
- Consistent issue backup, timers, safe restore and Fulcrum cutover tooling.
- Assembled Codex/Beads/Tollgate demonstration, long wait, hot reload, failure QA.
- Full normal-load performance matrix and requirement-by-requirement audit.

## Verification obligations

Each code commit must pass `scripts/check` and Tollgate before promotion.
The complete local/hosted check has a five-minute deadline. The current 79-test
suite passed locally in 168 seconds, alongside lint, Black, and strict Pyre.
Hosted macOS run 35832926989 reached the former three-minute deadline near the
end of the suite with every completed scenario passing; Linux passed. The
expanded allowance covers slower hosted database/CLI execution without removing
scenarios. Per-operation timeouts, assertions, and the ten-minute CI job limit
remain unchanged. This check budget is separate from task latency acceptance.
Meaningful integration tests must use disposable databases and projects.
Do not alter Fulcrum workers or its database to make tests convenient.

The <100ms p95 target is unproven until complete CLI operations meet it at eight
concurrent clients with 1,000 unfinished beads, observation and backup active.
Real 30-minute delivery wait and assembled agent lifecycle are also unproven.
The [warm read profile](read-latency.md) retains a reproducible disposable probe
and raw samples. The startup change reduced eight-client source-only p95 from
112ms to 75ms, but complete task reads/ready queries still measured 224/233ms.
This reduced fixture does not establish acceptance; native Beads reads alone
exceeded 100ms in both runs. No direct Dolt exception has been introduced.

Pause metadata now contains a nonempty array of distinct reason/note objects.
There is no legacy reader or automatic conversion. Hive production state has
not been initialized; disposable test stores are recreated. Any manually created
older Hive store requires explicit stopped maintenance before use.
