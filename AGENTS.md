# Working on Hive

Implement the complete Hive design referenced in README.md. Keep the core small;
Beads owns tasks and Tollgate owns ordinary code delivery. Do not add a dispatcher,
proof framework, task-store fallback, schema-version chain, or custom hashes.

Use an isolated Tollgate-created worktree. Every commit uses Conventional
Commits and must pass scripts/check and Tollgate before promotion. Use fresh
cold-review subagents for implementation diffs; give them the scope and diff,
not the author's conversation. Do not delegate implementation unless requested.

Run scripts/prepare-check after pyproject.toml or requirements-dev.lock changes.
Keep application state immutable and typed. No Any, unchecked cast, or broad
suppression in application code. Use black-box tests for behavioral guarantees.

Preserve local-master hot reload, consistent code/assets within a running call,
and connection continuity. Never activate ordinary edits by reinstalling or
restarting a resident service. Explicit dependency/state maintenance is separate.

Keep docs/implementation.md honest about outstanding requirements and evidence.
