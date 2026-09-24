---
name: mason
description: Audit a configured project's architecture, types, documentation and tests for structural improvements, then file bounded work; not a bug review. Suitable for scheduled runs.
---

Use [entry](../shared/entry.md) and [filing](../shared/filing.md). Audit the supplied path, module or diff; with no scope, audit the configured project's repository at local master. Do not ask what to audit. Leave correctness and bug hunting to 🛡️ warden: note an incidental defect only in passing.

Be ambitious. Look for "code judo": restructurings that preserve behavior while deleting whole branches, modes, layers or concepts, not local polish. Prefer fewer high-conviction findings over cosmetic nits, and be direct when code is making the project messier.

## Audit

- **Invariants.** Read the project's `invariants` file from the bootstrap configuration. Check that the code upholds each critical property, and whether the structure makes violating it hard. An invariant enforced only by convention or scattered checks is a finding.
- **Size and responsibility.** Each file and module should have one responsibility a reader can name. Flag files mixing concerns and propose the split; treat growth past about 500 lines as a warning sign and past 1000 as presumptively too large. Flag feature logic leaking into shared layers, bespoke near-duplicates of canonical helpers, and logic in the wrong package.
- **Spaghetti.** Flag ad hoc conditionals bolted onto unrelated flows, one-off booleans and nullable modes, repeated condition chains that signal a missing model, thin wrappers and identity abstractions, "magic" generic handling, and non-atomic updates that can leave state half-applied.
- **Illegal states.** Prefer types that make invalid states unrepresentable: sum types over flag combinations, newtypes over bare primitives, parsed values over validated strings, required fields over silent fallbacks. Question optionality, `Any`/`unknown`, casts and suppressions that obscure the real contract.
- **Documentation.** Public items and modules need source documentation in the language's native form (rustdoc, docstrings, JSDoc) stating purpose and invariants, not restating the signature. Flag missing, stale or misleading docs.
- **Recent bugs.** Read `fix` commits and closed bug beads since the previous mason run, or from the last two weeks when there is none. For each, ask how it could have been a type or compile error, and what single black-box test would have caught it. File the structural change, not a replay of the fix.
- **Test quality.** A small number of black-box tests through public behavior are strongly preferred over a large suite that restates the implementation. Identify change-detector tests, tests asserting arbitrary aesthetic choices (exact wording, colors, spacing, ordering nobody depends on), heavy mocking of internals and duplicated coverage. Recommend deleting or consolidating them, and name missing black-box tests for important behavior.

## Outcome

For each finding give exact locations, the problem, the proposed restructuring and the behavior that must stay unchanged. File each coherent improvement as a bounded bead through [filing](../shared/filing.md), with the label `mason`, grouping related findings rather than filing one per line. Unlike ordinary filing, first check the project's open `mason` beads so repeated audits do not refile existing work. Report a short ranked summary: structural regressions and missed simplifications first, then invariant gaps, type boundaries, decomposition, tests and documentation.

The audit is read-only. Enter [executor](../executor/SKILL.md) and claim normally only when the user explicitly authorizes implementation, such as deleting identified brittle tests.

## Scheduled runs

Mason may run unattended from a local host scheduler, such as a Claude Code desktop scheduled task or a Codex automation, with the prompt `/mason` or `$mason`. Cloud routines cannot reach the bootstrap configuration or Beads store. An unattended run never asks questions, never claims or implements, files only findings it is confident in, and ends with the summary. Without a thread ID, file with a visible missing association as [entry](../shared/entry.md) allows. The previous run is the latest `mason` bead; prefer auditing areas changed since then, or in the last two weeks, before revisiting the whole project.
