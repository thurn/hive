# Operations

## Work and repair

Use native `bd` with the [routing prefix](../skills/shared/routing.md) and an actual `--actor` for native create, claim, dependency and close commands. Inspect `show --json` before uncertain retries. Preserve another owner's assignment until its writer and external provider work are settled. If a prerequisite was cancelled, native readiness may still expose its dependent; inspect `hive_resolution` and revise or cancel the dependent deliberately. A paused bead requires explicit resumption. Claim before substantive work so its requests have an ownership interval. Follow-up work after a close belongs in a new bead rather than a reopen. Exceptional reopened work has its historical assignee and resolution cleared after coordinating affected workers, before another native claim.

For an incident, stop the affected writer, inspect the bead, worktree, candidate and provider status, and record native repair operations in a note. Coordinate server maintenance with all users. Leave ambiguous ownership in place rather than guessing that an old tool call stopped. No Hive-wide write stop or automatic takeover exists.

## Observation

Run `hive telemetry sweep --native-index PATH` manually or opt into `hive telemetry watch --native-index PATH --interval-seconds 5`. A host service can run that exact command with `HIVE_BOOTSTRAP_CONFIG` in its environment; stop it before editing service configuration or resetting state. Uninstall by removing that service definition. The derived database is `${state}/telemetry.sqlite3`; inspect its file size directly. To reset, stop the collector and remove only that file. This loses historical price evidence and cursors; source transcripts remain under Codex or Claude Code control. Collection failure never blocks native work. All canonical linked thread IDs are probed in both hosts, regardless of UUID version. Configure `claude_projects` in the bootstrap JSON to use a non-default Claude projects directory; the default is `~/.claude/projects`, and ambient `CLAUDE_CONFIG_DIR` does not redirect reads. Subagent files are discovered on every sweep and have separate cursors. Duplicate project matches and matches on both hosts are visible collection errors. A Codex index outage uses a previously discovered host/path, and otherwise waits for discovery to recover. `telemetry collect --task ID --transcript PATH` also accepts an individual Claude main or subagent file; filenames and embedded session identities must match. `telemetry usage --task ID` includes host and per-agent token counts, with input normalized to include all cache categories. `telemetry links --native-index PATH` and `cost --native-index PATH` can use a fixture or non-default Codex index for discovery.

Schema upgrades run atomically on collector writes, preserving historical quotes. A report that says “store not yet migrated” needs a `telemetry collect` or `sweep` using the current source. Migration waits up to five seconds for the write lock and otherwise reports `Busy` without partial changes; ordinary operations keep the 0.1-second lock timeout. An older call already in flight may fail once across this first schema transition; its transaction rolls back, and the next source-selected call retries. A versioned client refuses a store newer than it understands. Deleting the derived store remains an explicit reset, not a migration requirement.

Claude `cost --task ID` uses per-request observed modifiers and rejects an explicit `--tier`; Codex still defaults to `standard`. Claude totals are API-equivalent lower bounds, not subscription bills. Unknown models/modifiers, unsupported iterations and possibly partial output have separate counters. Rate evidence is retained on first successful quote storage; later output completion updates the amount using those original rates. The checked-in Claude prices were verified against [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) on 2026-09-24.

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

Claude host totals are compared only when one process start covers the recorded requests and its last timestamp follows every priced request, including subagents. `host_reported_reason` explains why comparison is unavailable (such as a resume, a stale total or missing records); Hive never sums process totals. `has_unknown_model_cost` means the host figure is itself incomplete. `unrecorded_usd_lower_bound` is disclosure only and is never attributed to a bead or tool.

Use `hive cost --task ID --requests --json` for up to 500 request rows, then pass `--cursor NEXT_CURSOR` with the same task and tier. Pages sort by response identity; new collection can change rows between calls, so paginate a quiescent thread for a stable export. Each cost component is a decimal USD string and unpriced requests have a reason. The SQLite `request_detail` view can be queried directly with `WHERE thread=? AND (tier IS NULL OR tier=?)`; this selects one Codex tier while Claude uses its observed modifiers. The view reads stored evidence; the CLI also prices rows whose evidence has not yet been retained, flagging `unretained` when contention defers storage. Event columns identify transcript-only, event-only and joined requests; parent-agent remains null until exact agent relationships are implemented.

## Optional Claude event receiver

Run `hive telemetry watch --native-index PATH --otlp-port 4319`; omitting the flag leaves the listener disabled, and supplying the flag without a number uses 4319. The resident listener binds only `127.0.0.1`. A port bind failure appears under `otlp_listener.error` in `hive telemetry status` while normal collection continues. Changing listener or timer code requires restarting this watcher; event parsing and other source code use the next source-selected batch.

Run `hive telemetry otlp-config --port 4319 --secret-file /absolute/private/new-file` to write the bearer secret into a new 0600 file and print settings with `<secret>` in its place. Existing files are not overwritten. Substitute that file's value manually into the printed `OTEL_EXPORTER_OTLP_LOGS_HEADERS` setting and add the `env` entries to Claude Code's settings. Hive never edits those settings. Apply them only to the logs signal, leave the metrics exporter unchanged, and keep `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT` and `OTEL_LOG_RAW_API_BODIES` off. Managed settings may remove exporter variables; only newly started Claude processes see configuration changes. Never paste the secret into a task or shell command that will be logged.

The secret lives at `${state}/otlp-secret` (0600). Raw payloads live at `${state}/otlp-spool/` (0700), with 0600 files renamed atomically from `.tmp` to `.json`. These raw bodies can contain personal attributes; ingestion will retain only approved fields. The receiver accepts `POST /v1/logs`, JSON content, a loopback Host and the bearer token, with 4 MiB bodies, eight connections, five seconds for headers and thirty for bodies. Spool saturation at 256 MiB or 65,536 bodies returns 503 and appends timestamps/counts to `rejections.log`; status shows spool bytes. A process that takes the port while Hive is stopped can receive the secret and payloads, so this endpoint is intended for a single trusted local user.

To reset the event receiver, stop the watcher, delete only `otlp-spool/`, `otlp-secret` and `otlp-status.json` under the configured state directory, regenerate the configuration, and replace the operator's saved secret. This discards queued events. To uninstall it, remove the printed logs settings from Claude Code and remove `--otlp-port` from the watcher command, then restart affected processes. Sweeps ingest even with no linked threads selected, with at most 16 MiB and two seconds per batch; large event batches retain a per-file record cursor. A malformed record does not discard its siblings. Raw bodies are deleted after committed observations; malformed files are replaced by a sanitized rejection marker. Raw/rejected files expire after seven days with a visible gap, and unlinked event rows expire after seven days. Retention is skipped if Beads refresh fails.

Claude cost reports now distinguish `priced_subset_usd` (transcript requests) from `event_only_usd` (requests seen only by OTLP), and expose `event_coverage`, missing sequences, unjoinable events, rejected records, join versions and token disagreements. `complete_estimate_usd` stays null with explicit reasons until coverage and evidence agree. Event-only TTL/geo derivation and fallback assumptions are listed; web searches and service tier are not observed by the event source. `api_error` events are counted but never billed. Listener rejection accounting includes a 30-second final-export grace period; unscoped malformed batches block completeness only for sessions overlapping their original receipt time, including that grace period. Pending raw batches also prevent a completeness claim. Timestamp-less Claude host totals use validated duration only to order updates, and report `cost_state_timestamp_unavailable` for comparisons: live resumes showed duration excludes idle time and cannot establish wall-clock coverage. Upgrades replay main files within normal collection budgets.

Claude `hive cost --task` includes `by_agent` and `by_skill`; their transcript rows partition `priced_subset_usd` exactly and retain unpriced counts. Each includes a separately labeled `event_only` row grouped by query source. Main has a null agent; missing skill is null and missing/ambiguous parent evidence is `"unknown"`. Subagent metadata is refreshed on collection, including manual collection; `--requests` exposes `parent_agent` using the same evidence. Schema 6 replays Claude files within ordinary budgets to recover spawning-tool identities.

Collector status includes `bead_events_caught_up`, `bead_events_behind`, `bead_events_error` and `interval_unknown_beads`. Ownership history comes from Beads events using bounded 500-row pages; after catching up the next scan overlaps ten minutes to catch late visibility. Beads schema/time failures preserve known former owners for collection and are visible independently of transcript costs. Event retention pauses while history is behind or its refresh fails.

`hive cost --bead ID [--tier T] --json` reports ownership-interval spend with exact host, model, hour, agent, skill and query-source partitions. The tier applies only to Codex requests. Shared claims split integer picodollars equally, with remainder units assigned in bead-ID order; `shared_requests` and requests within two seconds of interval boundaries are disclosed. Creator threads are context only. Requests without an ownership interval remain unowned; unknown ownership makes relevant spend unattributable. A pending replay blocks attribution and event retention until history catches up; stable unknown beads keep their assignees linked without blocking retention. Coverage flags apply to each contributing thread’s whole life.

`hive cost --reconcile [--tier T] --json` first retains missing quotes, then verifies attributed + unowned + unattributable equals retained thread costs, including event-only requests. Contention reports `unretained_estimates` and withholds a reconciliation result. This is a bookkeeping check, not an accuracy claim. Open intervals have a null end; deleted beads retain cached intervals with a deleted flag. Renames transfer charges only after the new ID certifies the same ownership evidence and intervals.

Beads operations `delete`, `prune`, `purge`, `rename-prefix`, `gc`, `compact`, `flatten`, backup/restore, and Dolt pull or sync from another machine can remove or hide ownership evidence. After these maintenance operations, run `hive telemetry reset-bead-events --json` and allow sweeps to catch up; this resets the local event cursor and requests per-bead rebuilds without deleting certified historical intervals. It cannot recover evidence already removed before Hive observed it. Hive reads only two fixed native SQL templates and never modifies Beads tasks during collection or cost reporting.


Claude thread cost reports now include estimated tool invocation/carrying costs and `allocation_buckets`; their sum reconciles exactly to `priced_subset_usd`. `unallocated_usd` and `allocation_error` expose arithmetic failures. This check establishes bookkeeping, not accuracy. `byte_split_error` is an in-sample tokens-per-byte fit from at least 20 single-part segments (otherwise null). Context resets and suspected prefix rewrites go to `rewritten_context`. The assumptions include byte weights, retained thinking, file order and 1h-before-5m write bands. Only prompt-visible byte lengths are stored; full `toolUseResult` previews and unrelated metadata do not contribute. `Agent.delegated_subagent_usd` is separate context already included in thread cost. Event-only requests have no transcript parts and stay outside thread tool allocation; bead tool breakdowns give them an explicit bucket. Upgrading replays Claude files incrementally within normal collection budgets; `allocation_missing_requests` discloses incomplete allocation evidence until replay finishes.

### Project-session observation

After dashboard observation step 1 is installed, a registered project's optional `observe_since` accepts an ISO date such as `2026-09-24`. Omit it to collect only linked sessions and their spawned Codex children. Do not add it to an older installation: older bootstrap settings reject unknown project keys on every call. New source-selected calls receive the registered projects automatically; no resident watcher restart is needed for application changes.

The collector resolves external Git worktrees through their common directory and keeps removed paths under a repository associated with that repository. Removed external worktrees without resolvable Git identity appear in `unresolved_cwd`. Inspect `hive telemetry status` for `discovery_behind` and `discovery_error`; a discovery failure preserves cached membership and skips event retention. Initial discovery can take several sweeps. Activity-first scheduling then prioritizes changed transcripts over idle history. Removing `observe_since` stops project-only collection; cached usage remains available and ordinary bead links still apply. Re-enabling observation rebuilds discovery eligibility.

### Read-only dashboard API

Run `hive dashboard api feed --json` for the latest collector-owned feed; add `--project hive`, `--window today|7d|30d`, `--role executor`, `--state Working`, `--active`, `--q TEXT`, or `--older-completed` as needed. Continue with the returned `--cursor` and the same filters. `hive dashboard api status --json` reports collection and summary freshness. The collector performs schema upgrades and summaries; API calls never initialize or repair the store.

Use `hive dashboard api bead ID --json`, `session THREAD --json`, or `ledger small_tails|unattributable PROJECT --json` for detail. A session detail includes the whole session unless `--tail` selects its tail. Bead requests are paged with `hive cost --bead ID --requests --json` or `hive dashboard api bead ID --requests --sort time|share --json`. Amounts and shares are exact decimal strings in picodollars. Excerpts require observed thread/call or event identities and revalidate native source identity; unavailable source evidence is reported instead of searching unrelated files. CI log tails require an already observed candidate and step.
