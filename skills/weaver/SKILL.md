---
name: weaver
description: Design a larger Hive project, test the document with a cold reader, obtain scope approval, and file linked implementation beads before entering executor.
---

Follow [role entry](../shared/entry.md) and use 🧵. Explore the project and ask
material design questions before committing to implementation scope. Usually
place the design in the project's `/plan` area, following its conventions.

Authoring a design is executable planning work. File and claim its planning bead
through normal admission before changing project files. Use a Tollgate worktree;
if capacity is full, file the planning bead and exit. Publishing a draft does not
approve its implementation. External design artifacts use artifact admission and
review instead of inventing a code candidate.

Write a standalone technical design with context, relevant references, concrete
behavior/interfaces, explicit constraints, failure handling, and acceptance
checks. Keep paragraphs short and examples useful. Cover the full agreed target;
do not silently narrow it to whatever is easiest to build.

Run a fresh cold-reader subagent with no forked history, only the document and
reading instructions. Ask it to explain the proposal and identify comprehension
gaps, undefined project-specific language, contradictions, and missing behavior.
Resolve material gaps and repeat once if substantial issues remain. For project
files, use [delivery](../shared/delivery.md), including the distinct fresh warden
diff review, to publish the authoring work. That delivery is not design approval.

Ask for approval of the concrete design, not another outline. Any implementation
beads filed before approval stay deferred for `design-approval`. Once approved,
use [filing](../shared/filing.md) for independently deliverable steps, acceptance
criteria, real dependencies, and links back to the approved document. Resolve
only that design's approval conditions; unrelated user pauses remain in effect.

Then read [Hive executor](../executor/SKILL.md) and enter normal admission. Do not
claim a new implementation slot while retaining the planning bead's ownership. Changes
to approved scope that need a material product decision require clarification.
