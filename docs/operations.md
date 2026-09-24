# Operations

## Work and repair

Use native `bd` with the [routing prefix](../skills/shared/routing.md) and an actual `--actor` for native create, claim, dependency and close commands. Inspect `show --json` before uncertain retries. Preserve another owner's assignment until its writer and external provider work are settled. If a prerequisite was cancelled, native readiness may still expose its dependent; inspect `hive_resolution` and revise or cancel the dependent deliberately. A paused bead requires explicit resumption. Reopened work has its historical assignee and resolution cleared before another native claim.

For an incident, stop the affected writer, inspect the bead, worktree, candidate and provider status, and record native repair operations in a note. Coordinate server maintenance with all users. Leave ambiguous ownership in place rather than guessing that an old tool call stopped. No Hive-wide write stop or automatic takeover exists.

## Observation

Run `hive telemetry sweep --native-index PATH` manually or opt into `hive telemetry watch --native-index PATH --interval-seconds 5`. A host service can run that exact command with `HIVE_BOOTSTRAP_CONFIG` in its environment; stop it before editing service configuration or resetting state. Uninstall by removing that service definition. The derived database is `${state}/telemetry.sqlite3`; inspect its file size directly. To reset, stop the collector and remove only that file. This loses historical price evidence and cursors; source transcripts remain under Codex control. Collection failure never blocks native work. Only Codex (UUIDv7) thread links are swept; other linked sessions appear in links and cost with `collected: false` or `usage_collectable: false` instead of as collector failures.

## Manual archive

Use `hive telemetry links --json` for the bead-linked candidate list, including closed beads, then check each thread with native activity tools (Codex tasks, or Claude desktop `get_session`; see the [archivist](../skills/archivist/SKILL.md) for Claude session ID mapping). Require no active or queued turn, no input/output/tool activity for 15 minutes, and known descendant effects. Recheck immediately before native archive. Skip unknown activity; a closed bead alone proves nothing. If activity races archival, unarchive the affected thread/descendants. Unlinked threads require explicit user scope.

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
