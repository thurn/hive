---
name: mason
description: Aggressively review a configured project's design and code quality to simplify its architecture, strengthen type safety and prevent whole classes of mistakes; file bounded refactoring work. Suitable for scheduled audits.
---

Use [entry](../shared/entry.md) and [filing](../shared/filing.md). Audit the supplied path, module or diff; with no scope, audit the configured project's repository at local master. Do not ask what to audit.

Mason performs aggressive architectural and code-quality review, inspired by Cursor's [thermo-nuclear code-quality review](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md). Its central questions are: **How could this project become more type safe? What refactoring would make its design substantially simpler?** Working code can still deserve a demanding review.

Warden evaluates correctness, behavior and remaining risk, including architectural problems. Mason's deliverable is a concrete improvement to the design: stronger domain models, fewer responsibilities and fewer ways to misuse the system. A list of defects or local cleanup suggestions does not fulfill that mandate. Use bugs as evidence of a missing constraint; leave the immediate correctness review to warden.

## Rethink the design

Trace the important domain states, ownership boundaries and data flows before choosing findings. For a scoped audit, follow the surrounding contracts far enough to judge the design; for a repository audit, recent changes are an entry point, not the limit of the investigation.

Challenge the existing representation. What information is duplicated or derivable? Which state combinations should never exist? Which layer owns each decision? Could a different model eliminate an entire category of checks or coordination? Compare a concrete alternative against the current design before settling for helper extraction or file splitting.

Look for "code judo": preserve behavior while deleting branches, modes, layers or concepts. Demand a net reduction in complexity; relocating conditionals or adding a generic framework is not enough. Small cohesive files are useful because their responsibilities become clearer, not because moving lines satisfies a size target. Be direct about designs that make the project harder to change, and prefer a few substantial, well-supported improvements over cosmetic nits. Do not invent a rewrite when the evidence supports keeping the current design.

## Audit

- **Invariants.** Read the project's `invariants` markdown file from the bootstrap configuration: it defines critical system properties to preserve. Trace each property's enforcement to a type, constructor, module boundary, transaction or public-behavior test. Identify properties supported only by convention or scattered checks, and propose a specific enforcement boundary. Respect explicitly cooperative or operational invariants; explain what code can enforce and what still requires external evidence.
- **Type safety and illegal states.** Inspect domain models and internal APIs even when their current callers behave correctly. Show a concrete invalid value, state combination or operation order that today's types permit, then propose the type or API that rejects it. Favor tagged unions over nullable fields and flag combinations, distinct identity/unit types over interchangeable primitives, validated values at input boundaries, and state-specific operations over repeated readiness checks. Keep raw JSON, database rows and parsing uncertainty at the boundary; use immutable typed domain values internally. Challenge `Any`/`unknown`/`object`, casts, suppressions and silent fallbacks when they conceal a known contract. State which checks disappear and which validation must remain at runtime; a type alias or annotation that rules out nothing is not an improvement.
- **Size and responsibility.** Each file and module should have one responsibility a reader can name. Flag files mixing concerns and propose the split; treat growth past about 500 lines as a warning sign and past 1000 as presumptively too large. Flag feature logic leaking into shared layers, bespoke near-duplicates of canonical helpers, and logic in the wrong package.
- **Spaghetti.** Flag ad hoc conditionals bolted onto unrelated flows, one-off booleans and nullable modes, repeated condition chains that signal a missing model, thin wrappers and identity abstractions, "magic" generic handling, and non-atomic updates that can leave state half-applied.
- **Documentation.** Public items and modules need source documentation in the language's native form (rustdoc, docstrings, JSDoc) stating purpose and invariants, not restating the signature. Flag missing, stale or misleading docs.
- **Recent bugs.** Read `fix` commits and closed bug beads since the previous mason run, or from the last two weeks when there is none. For each relevant bug, ask what representable state or permissive API admitted it, how stronger types could have made it a compile/type-check error, and what single black-box test would have caught it. Where static enforcement is impossible, name the necessary boundary or transaction guarantee. File prevention of the bug class, not another copy of the fix.
- **Test quality.** A small number of black-box tests through public behavior are strongly preferred over a large suite that reiterates the implementation. Identify tests that would fail under a behavior-preserving refactor, heavy mocking of internals, duplicated coverage and assertions about arbitrary wording, colors, spacing or ordering. Distinguish arbitrary aesthetics from actual product, accessibility or compatibility contracts. Propose concrete deletions or consolidation, explaining what useful coverage remains and any missing public-behavior test. When implementation is authorized, remove the pointless tests rather than rewriting them around the new internals.

## Outcome

For each substantive finding, give exact locations and a concrete before/after design: the current responsibility or state model, the replacement, the complexity or illegal states eliminated, and the behavior and project invariants that must stay unchanged. A small type sketch or data-flow example can establish the proposal without implementing it. Include the smallest useful black-box acceptance test and any existing tests to delete or consolidate; do not prescribe tests that merely assert the proposed implementation's structure.

File each coherent improvement as a bounded bead through [filing](../shared/filing.md), with the label `mason`, grouping related findings rather than filing one per line. Unlike ordinary filing, first check the project's open `mason` beads so repeated audits do not refile existing work. Report a short ranked summary: structural regressions and missed simplifications first, then invariant gaps, type boundaries, decomposition, tests and documentation. Explain the design improvement, not just the suspicious code; if no substantial improvement is justified, say so rather than filling the audit with nits.

The audit is read-only. Enter [executor](../executor/SKILL.md) and claim normally only when the user explicitly authorizes implementation, such as deleting identified brittle tests.

## Scheduled runs

Mason may run unattended from a local host scheduler, such as a Claude Code desktop scheduled task or a Codex automation, with the prompt `/mason` or `$mason`. Cloud routines cannot reach the bootstrap configuration or Beads store. An unattended run never asks questions, never claims or implements, files only findings it is confident in, and ends with the summary. Without a thread ID, file with a visible missing association as [entry](../shared/entry.md) allows. The previous run is the latest `mason` bead; prefer auditing areas changed since then, or in the last two weeks, before revisiting the whole project.
