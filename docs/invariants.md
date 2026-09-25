# Hive invariants

- Beads is the sole task and ownership store. Claims use `bd --actor <actual-thread-id> update <id> --claim` after agent inspection. Readiness, approval and workload checks are cooperative, not atomic with claiming.
- Tollgate owns worktrees, candidate delivery, testing, promotion and synchronization.
- Hive's source selector uses committed local `master` on every call. One running call keeps its imports and assets from that selected commit. A resident observer retains timer and connection continuity and may run an opt-in loopback log listener that authenticates and spools opaque bodies; event parsing stays in source-selected calls. Listener or timer edits need a watcher restart, while ordinary source edits do not.
- Skills route native Beads commands explicitly to one configured server database, with auto-start and ordinary remote auto-push disabled. Hive enforces this connection boundary only for its own read-only observation; task routing is an agent convention. No task operation depends on telemetry.
- Observation derives thread IDs only from bead creator metadata and native assignee fields, including closed beads. Costs are per thread, with associations, and missing coverage remains visible.
- The optional Claude event receiver may retain allow-listed request events for unlinked sessions for seven days so later Beads links can recover them; failed link refresh skips retention, and raw/rejected spool expiry is disclosed as a gap.
- Production activation, migration, and Fulcrum cutover require separate operator action.
