# Working on Hive

Implement [the scope reduction plan](docs/scope-reduction-plan.md). Hive supplies source selection, explicit Beads routing, bead-linked observation, and thread-level costs. Agents run the workflow; Beads owns tasks and atomic claiming; Tollgate owns delivery. Do not restore the retired coordination, session, delivery-policy, or hook subsystems.

Use an isolated Tollgate-created worktree. Every implementation commit uses Conventional Commits and passes `scripts/check` in Tollgate before promotion. During implementation use `scripts/check-fast` and relevant focused tests. Obtain a fresh cold-review subagent for each implementation diff, with scope, diff, invariants and checks but no author conversation. Do not delegate implementation unless requested.

Run `scripts/prepare-check` for a new environment or after `pyproject.toml` or `requirements-dev.lock` changes. Keep application state immutable and typed: no `Any`, unchecked casts, or broad suppressions. Use black-box tests for Hive-owned behavior.

Preserve local-master hot reload, consistent code/assets within a running call, and resident connection continuity. Ordinary source edits must not require reinstalling or restarting a resident service. Keep `docs/implementation.md` honest about evidence and outstanding acceptance. Do not activate production Hive or change Fulcrum state during implementation.
