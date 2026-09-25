# Executor stop check

Codex executors opt in for authorized execution with the configured Hive repository's
`bin/hive executor start --project PROJECT --json`. The command requires the actual
`CODEX_THREAD_ID` and a registered project; the project binding cannot change in
that session and survives deletion of an implementation worktree. Neither titles
nor historical bead links activate the check. Other roles and Claude Code are not
registered automatically.

`bin/hive executor hook-config --json` emits the three Codex hook entries to merge
into `~/.codex/hooks.json`: `Stop`, `UserPromptSubmit` and `Interrupt`. Preserve
existing entries and review/trust the new definitions through Codex's native hook
interface (`/hooks` in the CLI); never write trusted hashes yourself. All three
entries must be installed and trusted together so new input and interruptions
clear opt-in. The generated command uses the stable Hive launcher and explicit
bootstrap configuration, selecting local master on each call. It needs Python
3.12 and native `bd` on the hook environment's PATH. No daemon is installed.

The check reads assigned unfinished beads and unclaimed ready candidates in the
bound project using explicit native routing, with two seconds per Beads query.
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

Before intentionally ending, reconcile acceptance, delivery and the ready queue,
then use `bin/hive executor stop --reason 'CONCRETE EVIDENCE' --json`. This records
why the agent stopped and disarms the hook; it does not settle or release beads.
Scope judgments and the truth of the reason remain the agent's responsibility.
A missing or malformed binding, Beads outage or lock contention produces a warning
without a forced retry. A source-selection failure before the handler starts is
reported by the launcher as a hook failure. Other hooks may also affect termination.

The native hook contract and trust requirements are documented in
[OpenAI's hooks reference](https://learn.chatgpt.com/docs/hooks#stop).
