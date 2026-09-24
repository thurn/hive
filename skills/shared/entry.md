# Enter a Hive role

Read `~/brain/hive.json` (or `HIVE_BOOTSTRAP_CONFIG`) to identify the requested configured project, repository, invariants and optional native project. Read project instructions and relevant beads with native `bd` using [explicit routing](routing.md). Keep implementation in that project. If the binding is unclear, clarify before claiming; read-only scoping may continue.

Obtain the actual invoking thread ID from your own host's native context: `CODEX_THREAD_ID` in Codex, or `CLAUDE_CODE_SESSION_ID` in Claude Code. Never use the other host's variable, even when it is inherited. Expand it as `${VARIABLE:?}` in the same command as the Beads call so a missing ID fails instead of passing an empty actor. Do not invent one, reuse another thread's ID, or use a turn ID. A Claude Code Agent-tool subagent sees its parent's session ID, so it has no separate identity and must not claim or rename. If identity is unavailable, remain read-only or file what can be filed with a visible missing association; do not claim. Inspect existing assignments and outstanding tools on a returning thread before acting.

Rename your own thread directly with the host's native title tool: `set_thread_title` in Codex, or `set_session_title` with session `self` in a top-level Claude Code desktop app session. Plain Claude Code CLI sessions have no agent rename tool; report that and continue. A naming failure is visible and retryable but does not block delivery. Titles are UI, not observation membership.

Title a thread `<emoji> [<bead-id>] <summary>`, where the emoji is the role's (⚒️ executor, 📿 bead, 🛡️ warden, 🧵 weaver, 📖 sage, 🧱 mason, 🔮 vizier, 🔥 justiciar or 📁 archivist) and the summary is a short imperative phrase for the bead's work, usually its shortened bead title:

- On claiming or starting work on a bead: `⚒️ [hv-4up] Remove hive-bd wrapper`.
- After that bead closes as completed: `✅ [hv-4up] Remove hive-bd wrapper`.
- On moving to the next bead: that bead's working title, such as `⚒️ [hv-5xk] Add cost export`.
- Before any bead exists or is selected: `<emoji> <summary>` without brackets, such as `⚒️ Scope Hive task naming`.

Keep the same summary between the working and completed titles. Do not add the role name, `·` separators, outcomes or status words such as "complete"; for example, never `⚒️ executor · Scope plan removed · hv-bmx complete`.

Explicit user pauses and unapproved designs remain deferred until explicit resumption or approval. Readiness and resource judgment belong to the agent. Native claiming excludes a competing owner, but cannot force noncooperating agents to respect scope or pauses.
