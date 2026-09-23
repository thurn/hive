---
name: archivist
description: Archive only enrolled Hive conversations with more than fifteen minutes of verified inactivity and no active turn, respecting descendants and manual unarchive exemptions.
---

Follow [role entry](../shared/entry.md) and use 📁. This is bounded UI cleanup,
not cancellation, bead completion, or backlog recovery. It may run explicitly or
on its configured schedule; do not create a schedule just because the skill ran.

Read `hive session list --json` for the enrolled native task IDs. Do not discover
scope by matching emojis or titles. Never archive unregistered tasks or this
running archivist. Bead ownership and capacity are unaffected by archival.

For each candidate, obtain authoritative native input/output activity, current
turn/continuation state, affected descendants, and the registry's manual-unarchive
exemption/history. Require more than fifteen minutes without user input or agent
output, including tool activity, and no active turn. A pending CI/tool wait is
active even when it has produced no recent text. Include paused/input-waiting
conversations only after their turn ended and the inactivity rule is met.

Recheck just before native archival. Native archive may include descendants;
verify each affected child is eligible or skip the parent. Independent recruited
executors are separate roots. Unknown activity, descendants, or exemption state
means skip with an explicit observation gap, never assume idle or eligible.
The current registry does not yet expose archival history/exemptions; this path
must remain unavailable until that persistence and native observation integration
is implemented. Do not bypass the missing boundary with ad hoc metadata edits.

Use a native conditional archive if available. Otherwise reread after archiving
and unarchive tasks/descendants that became active or received input. Record Hive's
own race-repair unarchive so it is not confused with a manual exemption. This is
eventual UI repair, not atomic exclusion. Never stop a turn to make it archivable.

A manual unarchive exempts the conversation until the user opts it back in.
Ambiguous origin conservatively exempts it; missed observation intervals remain
visible gaps. Archive failure changes neither bead state nor ownership. Report
only useful cleanup results and actionable failures, not routine empty scans.
