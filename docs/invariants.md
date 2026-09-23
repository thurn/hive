# Hive invariants

[Scope reduction](scope-reduction-plan.md) is authoritative.

- Beads is the sole task and ownership store. Claims use `bd --actor <actual-thread-id> update <id> --claim` after agent inspection. Readiness, approval and workload checks are cooperative, not atomic with claiming.
- Tollgate owns worktrees, candidate delivery, testing, promotion and synchronization.
- Hive's source selector uses committed local `master` on every call. One running call keeps its imports and assets from that selected commit. A resident observer retains only timer and connection continuity.
- Explicit Beads routing uses one configured server database and disables ordinary auto-start, fallback and remote sync. No task operation depends on telemetry.
- Observation derives thread IDs only from bead creator metadata and native assignee fields, including closed beads. Costs are per thread, with associations, and missing coverage remains visible.
- Production activation, migration, and Fulcrum cutover require separate operator action.
