# Operations

## Work and repair

Use native `bd` with the [routing prefix](../skills/shared/routing.md) and an actual `--actor` for native create, claim, dependency and close commands. Inspect `show --json` before uncertain retries. Preserve another owner's assignment until its writer and external provider work are settled. If a prerequisite was cancelled, native readiness may still expose its dependent; inspect `hive_resolution` and revise or cancel the dependent deliberately. A paused bead requires explicit resumption. Reopened work has its historical assignee and resolution cleared before another native claim.

For an incident, stop the affected writer, inspect the bead, worktree, candidate and provider status, and record native repair operations in a note. Coordinate server maintenance with all users. Leave ambiguous ownership in place rather than guessing that an old tool call stopped. No Hive-wide write stop or automatic takeover exists.

## Observation

Run `hive telemetry sweep --native-index PATH` manually or opt into `hive telemetry watch --native-index PATH --interval-seconds 5`. A host service can run that exact command with `HIVE_BOOTSTRAP_CONFIG` in its environment; stop it before editing service configuration or resetting state. Uninstall by removing that service definition. The derived database is `${state}/telemetry.sqlite3`; inspect its file size directly. To reset, stop the collector and remove only that file. This loses historical price evidence and cursors; source transcripts remain under Codex or Claude Code control. Collection failure never blocks native work. All canonical linked thread IDs are probed in both hosts, regardless of UUID version. Configure `claude_projects` in the bootstrap JSON to use a non-default Claude projects directory; the default is `~/.claude/projects`, and ambient `CLAUDE_CONFIG_DIR` does not redirect reads. Subagent files are discovered on every sweep and have separate cursors. Duplicate project matches and matches on both hosts are visible collection errors. A Codex index outage uses a previously discovered host/path, and otherwise waits for discovery to recover. `telemetry collect --task ID --transcript PATH` also accepts an individual Claude main or subagent file; filenames and embedded session identities must match. `telemetry usage --task ID` includes host and per-agent token counts, with input normalized to include all cache categories. `telemetry links --native-index PATH` and `cost --native-index PATH` can use a fixture or non-default Codex index for discovery.

Schema upgrades run atomically on collector writes, preserving historical quotes. A report that says “store not yet migrated” needs a `telemetry collect` or `sweep` using the current source. Migration waits up to five seconds for the write lock and otherwise reports `Busy` without partial changes; ordinary operations keep the 0.1-second lock timeout. An older call already in flight may fail once across this first schema transition; its transaction rolls back, and the next source-selected call retries. A versioned client refuses a store newer than it understands. Deleting the derived store remains an explicit reset, not a migration requirement.

## Manual archive

Use `hive telemetry links --json` for the bead-linked candidate list, including closed beads, then check each thread with native activity tools (Codex tasks, or Claude desktop `get_session`; see the [archivist](../skills/archivist/SKILL.md) for Claude session ID mapping). Require no active or queued turn, no input/output/tool activity for 15 minutes, and known descendant effects. Recheck immediately before native archive. Skip unknown activity; a closed bead alone proves nothing. If activity races archival, unarchive the affected thread/descendants. Unlinked threads require explicit user scope.

## Remote sync

The Git repository containing `hive.json` (`~/brain` by default, which contains the Beads root) tracks the bootstrap configuration and the Beads root's tracked `.beads` files. Its private Git remote also stores the Dolt database under `refs/dolt/data`, added once with `bd dolt remote add origin git+ssh://git@github.com/OWNER/REPO.git`. Skills pass `--sandbox`, so nothing pushes automatically. Stop task writers first: skills write with auto-commit off, so `bd dolt commit` records whatever is in the shared working set. Then, abbreviating the [routing prefix](../skills/shared/routing.md), commit, push Dolt and push the Git branch:

```sh
bd dolt commit
bd dolt push
git -C /absolute/configuration-repository push origin master
```

Push from one machine only. A non-fast-forward rejection means another writer published; stop and investigate rather than forcing. Pulling onto another machine is restore-style maintenance with its writers stopped.

## Backup and restore

Stop task writers for a consistent native backup. With Beads 1.2.2 and Dolt 2.2.0, the verified server-mode commands, abbreviating the [routing prefix](../skills/shared/routing.md), are:

```sh
bd backup init /absolute/backup-directory --json
bd backup sync --json
```

For restoration, select a newly initialized, isolated destination store. Point `BEADS_DIR` at that store and verify `context --json` reports its database before running:

```sh
bd backup restore /absolute/backup-directory --force --json
bd list --all --limit 0 --json
```

Never restore over the live database. Compare descriptions, priorities, dependencies, metadata, comments, outcomes and historical assignees before making the restored store available. A disposable native round trip verified those fields; see [acceptance](acceptance.md).

## Cutover

Preserve Fulcrum's database, assignments, services, skill links and configuration. Install Hive skills only after resolving name conflicts, and first use an isolated Hive config/database. Refile selected intent with fresh bead IDs and links to originals only after Fulcrum stops executing that work. Activate production Hive, collector and any optional transport separately after acceptance. Rollback restores the prior skill links/configuration and stops the opt-in collector; it does not overwrite Fulcrum data.
