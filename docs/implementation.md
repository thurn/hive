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
  artifact completion. Negative Pyre fixtures reject interchangeable IDs.
  Database transactions and native stopped-writer checks remain adapter work;
  these tests do not establish end-to-end admission or recovery yet.
- Server-only Beads adapter, configuration, CLI, real concurrent database tests:
  native lifecycle/dependency JSON boundaries implemented and validated against
  observed server output. See `beads-boundary.md` for atomicity findings.
  Explicit server transport and native create/read/update primitives are now
  covered by disposable-server tests, including routing isolation, outage,
  retained lifecycle, dependency hydration, and uncertain responses. CI prepares
  pinned test binaries on Linux and macOS. Compound operations, configuration
  records, task CLI, and concurrent admission database tests remain.
- Local-master bootstrap, immutable source selection, maintenance write barrier.
- Tollgate workspaces, blocking delivery, recovery, and thin MCP transport.
- Native task registry, titles, recruitment, interruption and stop hooks.
- All eight skills and progressive-disclosure instructions.
- Archivist eligibility, descendant checks, race repair, manual unarchive.
- Incremental telemetry, traces, token pricing, coverage, and retention.
- Consistent issue backup, timers, safe restore and Fulcrum cutover tooling.
- Assembled Codex/Beads/Tollgate demonstration, long wait, hot reload, failure QA.
- Full normal-load performance matrix and requirement-by-requirement audit.

## Verification obligations

Each code commit must pass `scripts/check` and Tollgate before promotion.
Meaningful integration tests must use disposable databases and projects.
Do not alter Fulcrum workers or its database to make tests convenient.

The <100ms p95 target is unproven until complete CLI operations meet it at eight
concurrent clients with 1,000 unfinished beads, observation and backup active.
Real 30-minute delivery wait and assembled agent lifecycle are also unproven.
