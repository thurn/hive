# Hive

Hive is a small local companion to [Beads](https://github.com/steveyegge/beads) and Tollgate. Agents file, claim and close work with native Beads commands, and deliver code with native Tollgate commands. Hive selects committed local `master` for each call and observes Codex threads referenced by beads. The [scope reduction plan](docs/scope-reduction-plan.md) is the product contract; [implementation status](docs/implementation.md) records evidence and remaining acceptance.

## Configure

Use Python 3.12, Beads 1.2.2 in server mode, Dolt 2.2.0, and a configured Tollgate repository. Create `~/.config/hive/bootstrap.json` (or set `HIVE_BOOTSTRAP_CONFIG` for an isolated setup):

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

`bin/hive-bd` selects the configured store and forwards native `bd` arguments and streams. For example:

```sh
~/hive/bin/hive-bd --actor "$CODEX_THREAD_ID" list --all --json
~/hive/bin/hive-bd --actor "$CODEX_THREAD_ID" update hv-123 --claim --json
```

The actor for a claim must be the actual invoking Codex thread ID. Inspect scope, status, dependencies, outcome, and rough workload first. Native ownership excludes a competing actor, but readiness and approval checks are agent responsibilities. See [role instructions](skills/executor/SKILL.md) and [operations](docs/operations.md).

## Observe

```sh
~/hive/bin/hive source --json
~/hive/bin/hive telemetry links --json
~/hive/bin/hive telemetry sweep --native-index "$HOME/.codex/state_5.sqlite" --json
~/hive/bin/hive telemetry status --json
~/hive/bin/hive cost --task <thread-id> --json
```

The linked worklist comes from `hive_origin_thread` metadata and native assignees on open and closed beads. Cost is one API-equivalent total per thread, with associated beads and explicit gaps. An unlinked conversation is outside default collection and archival. `telemetry collect --task ID --transcript PATH` remains available for an operator-supplied thread. `telemetry watch` runs opt-in as a resident timer, launching a new source-selected batch each time. [Operations](docs/operations.md) covers reset and service setup.

## Install role skills

Run `~/hive/scripts/install-skills`. It links the eight skills and shared instructions from the stable Hive checkout into `${CODEX_HOME:-$HOME/.codex}/skills`. It refuses conflicting names and does not change Fulcrum's live setup. Use `--source` and `--dest` for a disposable trial. Review [cutover](docs/operations.md) before making production bindings.

## Develop

```sh
scripts/prepare-check
scripts/check-fast
scripts/check
```

The fast gate checks style, strict typing, boundaries, and retained accounting/link behavior. The full gate adds source selection and native routing integration. Tollgate runs the full gate against the actual integration candidate before promotion. New source commits are selected on the next call; existing calls retain their snapshot. Dependency or state maintenance is explicit.
