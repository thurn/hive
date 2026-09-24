# Implementation and acceptance status

The former Fulcrum design and Hive admission/session/delivery assertions are historical. Their deletion retires those requirements; it does not count as implementing them.

## Implemented in this branch

- Direct native Beads task commands, with shared skill instructions selecting the configured store through `BEADS_DIR` and disabling auto-start and auto-push. The former task launcher and argument-override parser are removed; task routing is an agent convention.
- Explicit configured server validation and environment sanitization remain for read-only bead observation.
- Source-selected local-master calls and immutable running-call snapshots.
- Bead-linked creator/assignee worklist including closed work, cached discovery across outages, and thread-level incremental usage/cost associations.
- Native role instructions, simple skill links, reduced fast/full gates, and manual operations guidance.

## Acceptance evidence and remaining limits

- Direct native task routing passed a disposable create/actual-thread claim/close/outage probe; it relies on agents not setting conflicting ambient routing variables. See `docs/acceptance.md`.
- Tollgate's native completion output was checked against a disposable two-candidate journey. Provider candidate `01a0cead-dfd8-73a2-9b0f-6976155138f0` corrected its missing local-master distinction, passed validation, was explicitly approved and promoted, and was installed from certified release `d6f14f827772937d05eb6d9c1faf3876762fffef`. After restart, native status reported `local_master.status=synchronized`, `contains_tested=true` and `policy_enabled=true` for that candidate; `tg --no-launch doctor` passed for the Tollgate repository.
- The disposable two-bead executor journey, distinct real-thread competing claim, native backup/restore round trip, real linked-thread observation comparison, collector restart, conservative active-thread archive skip and one-time 30-minute native wait/source update passed. No eligible disposable thread has been archived. See `docs/acceptance.md` for evidence and limits.
- Three quiescent runs passed the 30-second fast and 120-second full budgets; timings are in `docs/acceptance.md`.
- The opt-in collector command and stop/restart behavior were validated with disposable configuration; no host service was installed. Production activation and Fulcrum work migration remain separate operator actions.
- Claude Code workflow compatibility is instruction-level only: skills name `CLAUDE_CODE_SESSION_ID`, desktop session title/archive tools and `install-skills --agent claude`. No Claude Code executor journey, claim, rename or archive has been accepted. An Agent-tool subagent was observed to inherit its parent's `CLAUDE_CODE_SESSION_ID`, so subagents cannot be independent executors. Claude Code has no verified agent tool for creating separate sessions, and archive needs a mapping from the stored session ID to the desktop app's session ID. Telemetry and cost read only Codex transcripts and prices.

No assembled acceptance or production activation is implied by unit tests. Missing native activity evidence means archivist skips archival.
