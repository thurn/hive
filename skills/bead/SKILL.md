---
name: bead
description: Scope and file native Beads work with creator links and prerequisites.
---

Use [entry](../shared/entry.md) and [filing](../shared/filing.md). Preserve the enclosing role when invoked inline. Filing does not authorize implementation. Record actual creator thread when available, keep incomplete or unapproved work out of the selectable project list, and inspect the native result after multi-step filing.

After filing a ready bead (it has `hive_project` and no open prerequisites, deferral or pending approval), offer a new executor for it when there is capacity under the [executor](../executor/SKILL.md) guidance and the bead is still unassigned. Skip the offer when invoked inline by an executor that will select the work itself. Start one new top-level thread whose prompt invokes the executor skill for exactly that bead, such as `/executor hv-4up` in Claude Code or `$executor hv-4up` in Codex, naming the configured project:

- Claude Code desktop app: `spawn_task` titled with the bead's working title, such as `⚒️ [hv-4up] Remove hive-bd wrapper`. The user starts it by approving the chip.
- Codex app: `create_thread`, which starts the thread directly.
- Elsewhere, or if the tool is unavailable or fails: report the exact executor line for the user to start.

The new thread claims with its own identity; never claim on its behalf. Do not use subagents, scheduled routines or recurring tasks as executors.
