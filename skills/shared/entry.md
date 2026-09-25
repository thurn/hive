# Enter a Hive role

Read `~/brain/hive.json` (or `HIVE_BOOTSTRAP_CONFIG`) to identify the requested configured project, repository, invariants and optional native project. A project named in the invocation wins. Otherwise the configured project is the session's: the `projects` entry whose `repository` contains the directory the thread started in. For a linked worktree stored elsewhere, compare its main checkout, the parent of `git rev-parse --path-format=absolute --git-common-dir`. Resolve this once at entry, before changing directories, and keep it for the thread. If no entry or more than one matches, the binding is unclear.

Read project instructions and relevant beads with native `bd` using [explicit routing](routing.md). Keep implementation in that project. Separate epics or workstreams within it do not constitute cross-project execution, but user scope limits, pauses and approval holds still apply. Execution in another configured project requires explicit user authorization and resolution of that project's instructions and routing; a shared Beads store or infrastructure dependency does not grant it. If the binding is unclear, clarify before claiming; read-only scoping may continue.

Obtain the actual invoking thread ID from your own host's native context: `CODEX_THREAD_ID` in Codex, or `CLAUDE_CODE_SESSION_ID` in Claude Code. Never use the other host's variable, even when it is inherited. Expand it as `${VARIABLE:?}` in the same command as the Beads call so a missing ID fails instead of passing an empty actor. Do not invent one, reuse another thread's ID, or use a turn ID. A Claude Code Agent-tool subagent sees its parent's session ID, so it has no separate identity and must not claim or rename. If identity is unavailable, remain read-only or file what can be filed with a visible missing association; do not claim. Inspect existing assignments and outstanding tools on a returning thread before acting.

Rename your own thread directly with the host's native title tool: `set_thread_title` in Codex, or `set_session_title` with session `self` in a top-level Claude Code desktop app session. Plain Claude Code CLI sessions have no agent rename tool; report that and continue. A naming failure is visible and retryable but does not block delivery. Titles are UI, not observation membership.

Title a thread `<emoji> [<bead-id>] <summary>`, where the emoji is the role's (⚒️ executor, 📿 bead, 🛡️ warden, 🧵 weaver, 📖 sage, 🧱 mason, 🔮 vizier, 🔥 justiciar or 📁 archivist) and the summary is a short imperative phrase for the bead's work, usually its shortened bead title:

- On claiming or starting work on a bead: `⚒️ [hv-4up] Remove hive-bd wrapper`.
- After that bead closes as completed: `✅ [hv-4up] Remove hive-bd wrapper`.
- On moving to the next bead: that bead's working title, such as `⚒️ [hv-5xk] Add cost export`.
- Before any bead exists or is selected: `<emoji> <summary>` without brackets, such as `⚒️ Scope Hive task naming`.

Keep the same summary between the working and completed titles. Do not add the role name, `·` separators, outcomes or status words such as "complete"; for example, never `⚒️ executor · Scope plan removed · hv-bmx complete`.

Honor explicit user pauses and genuinely unapproved designs until resumption or approval; any native deferral requires [fresh justiciar agreement](repair.md#deferral-decisions). Check later user direction before treating an old approval note as a current hold. Readiness and resource judgment belong to the agent. Native claiming excludes a competing owner, but cannot force noncooperating agents to respect scope or pauses.
