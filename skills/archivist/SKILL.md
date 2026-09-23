---
name: archivist
description: Manual conservative cleanup of bead-linked Codex conversations.
---

Use [entry](../shared/entry.md). For authorized cleanup, get candidates from `hive telemetry links --json`, including creator and assignee links on closed beads. Deduplicate IDs. User-supplied threads may be added explicitly; do not search titles or the full conversation index for default membership.

For each candidate, require native evidence of no active or queued turn and at least 15 minutes without input, output or tool activity. Inspect descendants affected by native archive; unknown effects or activity mean skip. Recheck immediately before archive. Recheck afterward and promptly unarchive affected threads/descendants if activity raced it. Never stop a turn to make it eligible, and never change bead ownership. A closed bead alone proves nothing. Report capability limits when the native tools cannot establish safety.
