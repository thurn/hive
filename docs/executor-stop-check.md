# Executor stop check

Codex and Claude Code executors opt in for authorized execution with the configured
Hive repository's `bin/hive executor start --project PROJECT --session SESSION
--continuous --json` (or without `--continuous` for scoped execution). `SESSION` is
the invoking host's own native ID: `CODEX_THREAD_ID` in Codex, `CLAUDE_CODE_SESSION_ID`
in Claude Code. Without `--session`, the command falls back to whichever of the two
variables is set, and refuses when both are set and differ, because Claude Code can
inherit a Codex thread's variable. Claude Code subagents inherit their parent's
session ID and never opt in. The project binding cannot change in that session and
survives deletion of an implementation worktree. Neither titles nor historical bead
links activate the check. Other roles are not registered automatically. The executor skill starts with `--continuous` by default:
unfinished assignments and unclaimed ready beads in the bound project both trigger
continuation. A start without `--continuous` selects scoped execution, where only
unfinished assignments do; the skill uses it only when the user explicitly limits
the request to named work. Each start sets the mode explicitly, and older activation
records default to scoped execution.

`bin/hive executor hook-config --json` emits the three Codex hook entries to merge
into `~/.codex/hooks.json`: `Stop`, `UserPromptSubmit` and `Interrupt`.
`bin/hive executor hook-config --host claude-code --json` emits the `Stop` and
`UserPromptSubmit` entries for the `hooks` object of `~/.claude/settings.json`.
Claude Code has no interrupt hook, so its next prompt disarms instead. Its Stop input
identifies the turn with `prompt_id` rather than `turn_id`; the handler accepts either.
Its `UserPromptSubmit` also fires for prompts the host injects; those are recorded
as `injected` and do not disarm. Injection is recognized by a `source` of `system`,
`loop_wakeup`, `schedule_wakeup` or `poll_event`, or, because Claude Code 2.1.293
sends no `source`, by a prompt beginning with `<task-notification>`, which a finished
background subagent, shell, monitor or workflow submits. Other host-injected prompts,
such as `/loop` or `ScheduleWakeup` firings and cross-session messages, carry no
marker the hook receives and still disarm.
Claude Code also ends a turn to wait for background work. A Stop listing a
`background_tasks` entry of type `shell`, `subagent`, `monitor` or `workflow`, or a
non-recurring `session_crons` wakeup, is recorded as `waiting`: no correction or
warning, and the correction budget is unchanged. Other task types (such as `dream`,
`auto-mode scan` and `teammate` entries) and recurring crons do not count,
because they would hold the check off without waking the session. A one-shot
wakeup (`ScheduleWakeup` or a dynamic `/loop`) does count, but its firing carries
no marker and disarms, so an executor that ends a turn on one gets no correction
and is then disarmed. The check runs
again at the next turn end without such work, so a process left running
indefinitely, such as a background dev server or an ambient monitor (the hook cannot
tell those apart from monitors the agent started), suppresses it until it ends. Preserve existing entries. For Codex, review/trust the new
definitions through its native hook interface (`/hooks` in the CLI); never write
trusted hashes yourself, and install all three entries together so new input and
interruptions clear opt-in. Claude Code has no trust step: merge both entries into
`~/.claude/settings.json` together and confirm them with `/hooks`. The generated command uses the stable Hive launcher and explicit
bootstrap configuration, selecting local master on each call. It needs Python
3.12 and native `bd` on the hook environment's PATH. No daemon is installed.

The check reads assigned unfinished beads and, only in continuous mode, unclaimed
ready candidates in the bound project using explicit native routing, with two seconds per Beads query.
Closed and deferred beads and competing owners do not trigger continuation.
Readiness is only a candidate signal: the agent still checks scope, prerequisites'
completed outcomes, approvals and resource pressure before claiming. The hook
never modifies Beads, claims work, starts a server or releases an assignment.

On premature stopping it returns one corrective prompt. Repeated stops in that
execution return a visible warning; `stop_hook_active` also prevents correction.
The exact generated continuation prompt preserves opt-in if Codex delivers it
through `UserPromptSubmit`; ordinary user input disarms it, allowing read-only
follow-ups and explicit pauses. An interrupt disarms without restarting work.
Concurrent new input invalidates a slow stop check before it can request continuation.
New authorized executor turns explicitly opt in again.

Before intentionally ending, reconcile acceptance and delivery and inspect the ready
queue. No eligible bead remaining is a `drained` stop. A `scope` stop applies only when the user
explicitly limited the request to named work, even if unrelated project work remains
ready.
For a perceived blocker, retry cutoff, timing miss or pressure, first invoke
justiciar in the same task using the [recovery protocol](../skills/shared/repair.md#executor-recovery-before-stopping).
Inspect evidence and requirement authority; repair or record an authorized relaxation
of agent-imposed constraints, then resume executor work. Explicit user acceptance,
pauses and approval holds remain binding. Exhausting identical retries does not
exhaust useful diagnosis. Only an unresolved external dependency or capacity limit
after recovery permits checkpointing, settling writers and considering independent work.

Use `bin/hive executor stop --kind KIND --reason 'CONCRETE EVIDENCE' --session SESSION --json`.
Kinds are `pause`, `approval`, `drained`, `scope`, `blocked` and `pressure`.
The last two also require `--recovery 'EVIDENCE, REPAIR/RELAXATION, REMAINING LIMIT'`.
A missing category or empty required recovery account fails before disarming, so
the old free-form blocker command cannot bypass recovery. Valid stops record the
category, reason and recovery account in local activation state and disarm the hook;
they do not settle or release beads. New input and interrupts still disarm immediately.
This is an agent attestation, not proof that justiciar ran: truthful classification,
adequacy of recovery and permission to relax a requirement remain agent judgments.
The handler does not parse transcripts or mutate Beads to enforce them. Correction
remains bounded even if an agent ignores the protocol.
A missing or malformed binding, Beads outage or lock contention produces a warning
without a forced retry. A source-selection failure before the handler starts is
reported by the launcher as a hook failure. Other hooks may also affect termination.

The native hook contract and trust requirements are documented in
[OpenAI's hooks reference](https://learn.chatgpt.com/docs/hooks#stop) and
[Claude Code's hooks reference](https://docs.claude.com/en/docs/claude-code/hooks).

## Diagnostic receipts

`bin/hive executor diagnostics --json` reads the last 128 receipts across all
sessions; add `--session NATIVE_UUID` to filter that bounded history. The private
`executor-diagnostics.json` file in configured Hive state is capped at 256 KiB.
Atomic replacement and a separate 50 ms lock keep diagnostics independent of
activation and native Beads operations. Logging failures add a visible warning
without changing a decision or preventing start, stop or new-input disarming.

Each receipt contains its UTC timestamp, selected source commit, native session
and turn UUID when valid, event, fixed outcome code and intentional stop category.
Activation and intentional stop receipts survive resume until aged out of the
global ring. A handler invocation is recorded before native reads and a decision
afterward: unbound, inactive, waiting, drained, correction, warning, superseded,
disarmed, continuation-preserved, injected or unavailable. No prompt, assistant response, provider
payload or free-form stop/recovery text is copied into this diagnostic log.
Malformed identity fields are omitted. The activation file still holds the latest
explicit stop reason until resumed, as before.

A host that never launches the command cannot produce a receipt. An invocation
without a later decision can mean interruption, timeout or a diagnostic failure;
absence can also mean retention expiry, storage failure or a launcher failure
before the handler starts. Direct calls produce receipts too, so receipts alone
cannot establish host provenance. Correlate session/turn/time with a supported
host-driven exercise and its output. Installed, currently trusted and actually
invoked are separate claims; none proves historical invocation. Inspect current
trust through Codex `/hooks`, never by writing trust hashes or using bypass flags.
