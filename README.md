# Hive

Hive is a small local companion to [Beads](https://github.com/steveyegge/beads) and Tollgate. Agents file, claim and close work with native Beads commands, and deliver code with native Tollgate commands. Hive selects committed local `master` for each call and observes Codex and Claude Code threads referenced by beads. Costs include per-request detail, Beads ownership attribution, exact Claude subagent/skill breakdowns and estimated tool allocation. [Implementation status](docs/implementation.md) records evidence and remaining acceptance.

## Configure

Use Python 3.12, Beads 1.2.2 in server mode, Dolt 2.2.0, and a configured Tollgate repository. Create `~/brain/hive.json`, versioned beside the Beads data (or set `HIVE_BOOTSTRAP_CONFIG` for an isolated setup):

```json
{
  "repository": "/Users/dthurn/hive",
  "state": "/Users/dthurn/.local/state/hive",
  "beads": "/Users/dthurn/brain/hive",
  "projects": [
    {
      "id": "example",
      "repository": "/absolute/project/path",
      "invariants": "/absolute/project/path/AGENTS.md",
      "native_id": "optional-codex-project-id"
    }
  ]
}
```

Defaults without a file are `~/hive`, `~/.local/state/hive`, and `~/brain/hive`, with no registered projects. The Beads directory must already contain explicit server-mode `.beads/metadata.json`. Hive never initializes it, starts a replacement server, or falls back to another database. Change project/routing settings only during stopped maintenance with participating writers settled.

Agents run native `bd` directly, selecting the configured store with `BEADS_DIR` as described in the [shared routing recipe](skills/shared/routing.md). With the default store:

```sh
BEADS_DIR=~/brain/hive/.beads BEADS_DOLT_AUTO_START=0 BD_NON_INTERACTIVE=1 BD_NO_HOOKS=true bd --sandbox --dolt-auto-commit off --actor "${CODEX_THREAD_ID:?}" list --all --json
BEADS_DIR=~/brain/hive/.beads BEADS_DOLT_AUTO_START=0 BD_NON_INTERACTIVE=1 BD_NO_HOOKS=true bd --sandbox --dolt-auto-commit off --actor "${CODEX_THREAD_ID:?}" update hv-123 --claim --json
```

The actor for a claim must be the actual invoking thread ID: `CODEX_THREAD_ID` in Codex or `CLAUDE_CODE_SESSION_ID` in Claude Code, never the other host's inherited variable. Agent-tool subagents in Claude Code share their parent's ID and must not claim. Inspect scope, status, dependencies, outcome, and rough workload first. Native ownership excludes a competing actor, but readiness and approval checks are agent responsibilities. See [role instructions](skills/executor/SKILL.md) and [operations](docs/operations.md).

## Observe

```sh
~/hive/bin/hive source --json
~/hive/bin/hive telemetry links --json
~/hive/bin/hive telemetry sweep --native-index "$HOME/.codex/state_5.sqlite" --json
~/hive/bin/hive telemetry status --json
~/hive/bin/hive cost --task <thread-id> --json
~/hive/bin/hive cost --task <thread-id> --requests --json
~/hive/bin/hive cost --bead <bead-id> --json
~/hive/bin/hive cost --reconcile --json
```

The linked worklist comes from `hive_origin_thread` metadata, current native assignees and historical Beads ownership. Cost is observed per thread, with explicit coverage gaps; requests are attributed only to beads held at the request time, with equal sharing for overlapping claims. Claude discovery defaults to `~/.claude/projects` and can be routed with `claude_projects` in the bootstrap settings. The optional authenticated loopback OTLP receiver supplements transcripts; enabling it does not change Claude settings. Tool allocations are estimates, and exact bucket reconciliation does not establish their accuracy. An unlinked conversation is outside default collection and archival. `telemetry collect --task ID --transcript PATH` remains available for an operator-supplied thread. `telemetry watch` runs opt-in as a resident timer, launching a new source-selected batch each time. [Operations](docs/operations.md) covers reset and service setup.

## Install role skills

Run `~/hive/scripts/install-skills`. It links the nine skills and shared instructions from the stable Hive checkout into `${CODEX_HOME:-$HOME/.codex}/skills`, or with `--agent claude` into `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills`. It refuses conflicting names and does not change Fulcrum's live setup. Use `--source` and `--dest` for a disposable trial. Review [cutover](docs/operations.md) before making production bindings.

## Develop

```sh
scripts/prepare-check
scripts/check-fast
scripts/check
```

The fast gate checks style, strict typing, boundaries, and retained accounting/link behavior. The full gate adds native Beads history/attribution, Claude events/tool allocation, source selection and observer routing integration. Tollgate runs the full gate against the actual integration candidate before promotion. New source commits are selected on the next call; existing calls retain their snapshot. Dependency or state maintenance is explicit.

Tollgate and GitHub both run `scripts/prepare-check && scripts/check`. Validation requires the exact Python version in `.python-version` and Node version in `dashboard/.nvmrc`, with locked Python/npm dependencies and pinned Beads/Dolt tools. The shared check runner sets UTC, the C locale, UTF-8 Python I/O and a fixed Python hash seed; timezone-specific fixtures explicitly select their own timezone. These checks run natively on macOS and Linux, so wall-clock speed still depends on the machine; the same performance limits apply on both. GitHub remains an independent check after push, with no remote wait in Tollgate.
The executor workflow can be exercised end to end by filing, claiming and delivering a trivial bead.
