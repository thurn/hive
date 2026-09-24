# Use Beads directly

Read `${HIVE_BOOTSTRAP_CONFIG:-$HOME/.config/hive/bootstrap.json}`. Its `beads` field is the absolute store root (default `$HOME/brain/hive`), not the implementation checkout. Never infer the store from cwd: a Tollgate worktree or project checkout may have its own `.beads`.

Run every native command with `BEADS_DIR` naming that root's `.beads` directory, auto-start, prompts and hooks disabled, and the flags Hive has always used:

```sh
BEADS_DIR=/Users/me/brain/hive/.beads BEADS_DOLT_AUTO_START=0 BD_NON_INTERACTIVE=1 BD_NO_HOOKS=true bd --sandbox --dolt-auto-commit off --actor "${CODEX_THREAD_ID:?}" show hv-123 --json
```

Use the literal configured path. In Claude Code the actor is `"${CLAUDE_CODE_SESSION_ID:?}"`; see [entry](entry.md). Skill and runbook `bd` examples abbreviate this prefix; always include it.

- `--sandbox` disables ordinary remote auto-push; backup and sync are explicit maintenance.
- `BEADS_DOLT_AUTO_START=0` makes an unavailable server fail. Stop there; never start another server, initialize a store or fall back.
- Do not add `--db`, `--global`, `-C`, `--repo`, `--sandbox=false` or another auto-commit setting, and do not rely on ambient `BEADS_*` or `DOLT_*` settings other than a supplied password.
- If routing is in doubt, `bd context --json` with the same prefix (run from a Git checkout) must report the configured `beads_dir`, `database` and server from `.beads/metadata.json`.
