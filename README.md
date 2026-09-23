# Hive

Decentralized agent workflows using a shared server-mode Beads database and
Tollgate delivery. Workers own progress; there is no central dispatcher.

Implementation is in progress. The source of requirements is
[the Hive design](https://github.com/thurn/fulcrum/blob/master/hive-design.md).
[Implementation status](docs/implementation.md) records completed work and
remaining acceptance evidence without treating partial scaffolding as delivery.

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
