---
name: executor
description: File, claim, implement, review, deliver, close and continue eligible Hive work in one configured project.
---

Use [entry](../shared/entry.md), [filing](../shared/filing.md) and [delivery](../shared/delivery.md). Scope and file native work, or inspect an explicitly supplied bead. Check project, status, dependencies and recorded outcomes, approvals, active assignments and resource pressure. Eight assigned unfinished beads is guidance, not a gate.

Claim exactly the selected bead using `hive-bd --actor <actual-thread-id> update <bead-id> --claim --json`. Proceed only on acknowledgement. A competing owner wins through Beads; never overwrite it. A failed explicitly requested initial claim does not authorize unrelated implementation. Aim for one unfinished assignment per thread. On a later turn, inspect assignment and outstanding tools before resuming.

Schedule warden review only for substantial changes under the project's review policy. By default, substantial means **more than 100 non-test lines of code changed**, counting additions plus deletions across the complete implementation diff against its base, not per commit or net growth. Test and documentation changes do not count toward that default. Explicit per-project policy (such as `AGENTS.md`) overrides the default threshold or criteria, including requiring review for every change. When review is required, follow the cold-review procedure in [delivery](../shared/delivery.md).

Deliver natively, settle writers and external outcomes, record `hive_resolution`, close, rename directly, then inspect the next eligible bead in the same project, starting from `hive-bd ready --metadata-field hive_project=<project-id> --json`. Continue until paused, blocked, outside scope or imprudent under current pressure. Use [repair](../shared/repair.md) for uncertainty. There is no dispatcher or stop-hook reminder.
