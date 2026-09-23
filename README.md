# Hive

Decentralized agent workflows using a shared server-mode Beads database and
Tollgate delivery. Workers own progress; there is no central dispatcher.

Implementation is in progress. The source of requirements is
[the Hive design](https://github.com/thurn/fulcrum/blob/master/hive-design.md).
[Implementation status](docs/implementation.md) records completed work and
remaining acceptance evidence without treating partial scaffolding as delivery.

## Source-selected command

`~/hive/bin/hive source --json` reports the local-master commit used for the
invocation. This diagnostic works without a task server. The task commands use
the explicitly configured server-mode Beads database; see
[command usage](docs/commands.md) and [task names](docs/task-ui.md).

The launcher uses Python 3.12 and reads `~/.config/hive/bootstrap.json` when
present. Defaults select `~/hive`, `~/.local/state/hive`, and `~/brain/hive`
for source, local state, and explicit Beads routing respectively. These paths
do not initialize storage or adopt the existing Fulcrum database. An explicit
`HIVE_BOOTSTRAP_CONFIG` path selects an isolated configuration for tests.

```json
{
  "repository": "/Users/dthurn/hive",
  "state": "/Users/dthurn/.local/state/hive",
  "beads": "/Users/dthurn/brain/hive"
}
```

All workers on a host must use the same configuration and local state directory.
The launcher checks local `master` on every call, prepares a snapshot on a cache
miss, and starts isolated application imports from that snapshot. Existing
operations retain their code and assets. Preparation failure is visible; it
never falls back to older code. Ordinary code commits need no installation or
restart. Runtime dependency changes require explicit environment maintenance;
the current runtime has no third-party dependencies.

The bootstrap itself contains only source selection and guard transfer. It
must not import task policy or manage workers. Prepared snapshots are retained
so an active invocation cannot lose delayed imports or assets; automatic source
cache pruning is not currently provided.

## Development

Use Python 3.12. Install the pinned development environment, then run all checks:

```sh
scripts/prepare-check
scripts/check
```

The same entrypoint runs Ruff lint, Black formatting checks, strict Pyre,
boundary rules forbidding unchecked typing escape hatches, unit tests, and real
Beads/Dolt server tests in Tollgate and GitHub CI. Preparation reuses matching
Beads 1.2.2 and Dolt 2.2.0 binaries or downloads them into the ignored test-tool
directory. Tests start temporary servers and never use the production database.
Checks do not rewrite source files. Formatting is explicit:

```sh
.venv/bin/python -m black src tests scripts
```

Regenerate `requirements-dev.lock` from `pyproject.toml` with `uv pip compile
pyproject.toml --extra dev --no-header --no-annotate -o requirements-dev.lock`,
then run `scripts/prepare-check` again. Keep the editable package and dependencies
in sync after dependency changes.
