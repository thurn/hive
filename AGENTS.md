# Working on Hive

Hive supplies source selection, explicit Beads routing, bead-linked observation, and thread-level costs. Agents run the workflow; Beads owns tasks and atomic claiming; Tollgate owns delivery.

Use an isolated Tollgate-created worktree. Every implementation commit uses Conventional Commits and passes `scripts/check` in Tollgate before promotion. During implementation use `scripts/check-fast` and relevant focused tests. Obtain a fresh cold-review subagent for each implementation diff, with scope, diff, invariants and checks but no author conversation. Do not delegate implementation unless requested.

Run `scripts/prepare-check` in each new worktree before `scripts/check-fast`, and again after `pyproject.toml` or `requirements-dev.lock` changes. Run Tollgate waits from the main checkout, since promotion removes the clean worktree. Keep application state immutable and typed: no `Any`, unchecked casts, or broad suppressions. Use black-box tests for Hive-owned behavior.

Preserve local-master hot reload, consistent code/assets within a running call, and resident connection continuity. Ordinary source edits must not require reinstalling or restarting a resident service. Keep `docs/implementation.md` honest about evidence and outstanding acceptance.

When completing tasks, provide a short summary of the work with the title "# Summary" as flat bullet points, focusing on surprising or interesting decisions you made, problems encountered, and action items for me. Flat means no nested bullets, and each bullet is a single sentence.
