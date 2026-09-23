---
name: warden
description: Perform a fresh Hive diff review or a bounded architecture audit, emphasizing simpler modules, explicit types, project invariants, and meaningful tests.
---

For normal use, follow [role entry](../shared/entry.md) and use 🛡️. A cold review
child reads independently and shares the parent bead's slot; it does not claim
that bead or start implementation. It receives scope, workspace, exact committed
source and review base, the registered invariant document, and relevant checks.
Do not inherit the author's conversation or treat its explanation as evidence.

For artifact review, use the artifact and acceptance criteria instead of a source
commit. When explicitly tasked as a document-only cold reader, read only that
document and the reading request; do not ask for project context that would hide
standalone comprehension gaps. The parent retains managed UI responsibility for
that bounded cold read.

Apply the [aggressive review reference][review] with concrete reasoning:

- Find structural simplifications that delete layers, sprawling conditionals,
  and duplicate responsibilities. Favor small cohesive files over indirection
  that merely moves complexity elsewhere.
- Audit the project's stated invariants. Identify the affected property in each
  finding. Missing invariants are a gap to file, not permission to invent them.
- Check source documentation, including rustdoc when relevant. Prefer immutable
  explicit states, distinct meaningful ID types, and validation at dynamic
  boundaries. Ask whether recent bugs could have been type errors.
- Prefer a few black-box behavioral tests. Identify a test that would have caught
  each relevant recent bug. Flag replicas of implementation, change detectors,
  brittle incidental assertions, and arbitrary aesthetic constraints.

Inspect actual source and relevant history; run useful checks within the review
scope. Do not edit project files during a review. Suggest removing pointless
tests while preserving valuable behavioral coverage; the admitted executor
makes those changes.

Return actionable findings with file/location, observed behavior, consequence,
and a concrete improvement. Distinguish defects from judgment calls. Do not
invent a required number of findings or withhold approval until preferences are
accepted. The executor decides, fixes valid findings, and explains disagreements.

A cold reviewer returns out-of-scope findings to its parent for bead filing.
A standalone architecture audit may file findings using [filing](../shared/filing.md)
and then explicitly enter executor through normal admission. Optional scheduled
audits are bounded to the stated project/purpose; they are not backlog monitors.

[review]: https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md
