# Incremental native usage collection

The derived SQLite store ingests bounded native transcript chunks independently
of Beads. It retains only response identities, owning task/turn, observation
time, usage counters, source progress, and parsing failures. It never retains
message text, tool input/output, or reasoning content. It has no task authority.

These source-selected diagnostic operations are available:

```sh
hive telemetry collect --task <native-task-id> --transcript <native-jsonl> --json
hive telemetry usage --task <native-task-id> --json
```

Each collection call reads at most 1 MiB of new transcript data plus a bounded
native session header. It verifies the native identity rather than trusting a
filename. Complete records, usage, and the new byte offset commit in one SQLite
transaction. An incomplete trailing record stays at its original offset until
the next call. Records larger than 256 KiB are skipped with a visible gap;
continuing a large record cannot allocate unbounded memory or stall the cursor.

Ordinary task operations do not import or call this collector. Explicit
transcript collection and usage reporting do not open Beads or retain the task
maintenance guard. SQLite lock waiting is bounded to 100ms; a failed collection
can be retried separately.
The maximum byte budget can be lowered with `--max-bytes` to any value from
262145 through 1048576. This is an observation batch bound, not task latency.

## Native evidence and deduplication

On September 23, inspection of the installed desktop's own transcript exposed
`token_usage_record` entries with native `response_id`, `thread_id`,
`session_id`, `turn_id`, and per-response `usage`. That exact shape has a narrow
typed decoder. Native session metadata is required at the start of a file.
The local app-server schema also exposes a response-completed notification,
but this reader does not depend on a live subscription or invent that access.

Use the response ID once across archive moves, replay, and process restart.
Cumulative `token_count` events and copies inside compaction records are not
additional responses. Conflicting owners or token counts for the same response
produce a gap instead of overwriting known usage. A later record can supply
previously missing usage; a missing duplicate cannot erase known counters.
Native counters must fit the provider's signed 64-bit range. Accumulated totals
use exact Python integer arithmetic so many valid responses cannot overflow a
SQLite aggregate or silently become imprecise floating-point estimates.

Native archive moves preserving the file identity resume at the existing offset.
A different file identity or truncation restarts reading, deduplicates responses,
and records that earlier coverage may be missing. Files are expected to be
append-only between native moves; arbitrary in-place rewrites that preserve
identity and size cannot be detected by this cursor. There is no content hash.

Missing files retain previously collected usage and report an error. Remaining
bytes and incomplete-tail status are unknown when the source cannot be read.
Malformed records are counted as gaps and do not prevent later valid records
from being collected. The report includes the last 20 gap details and offsets.
Unknown usage stays distinct from observed zero.

## Enrolled tasks and background collection

A bounded sweep refreshes enrollment through supported Beads reads, selects the
least recently attempted conversations, and looks up their current transcript
paths in the native Codex SQLite index. It then ingests one bounded chunk per
task. Missing or malformed sources cannot starve the next task. The index is
opened read-only; each transcript must still prove its native task identity.

```sh
hive telemetry sweep --native-index <absolute-native-index-path> --json
hive telemetry watch --native-index <absolute-native-index-path> --json
hive telemetry status --json
```

Supply the native index explicitly. The official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
describes `sqlite_home`, but does not promise its internal schema. On September
23 the installed index contained `threads(id, rollout_path)` and returned this
conversation's actual transcript. Only that observed path lookup is supported;
schema changes fail visibly. There is no directory scan, index-version guessing,
activity inference, or write to Codex's database. A native public path-discovery
operation can replace this narrow boundary when available.

The batch defaults to 32 tasks, accepts 1–64, and uses a five-second cooperative
collection deadline. Each task reads at most 1 MiB plus its bounded session
header. Native path queries have a 100ms lock timeout and 250ms execution guard.
The Beads enrollment read has a two-second provider timeout. Enrollment itself
is a complete supported Beads read; its cost still grows with the registry.
The transport's 30-second child deadline also bounds startup and failed calls.
These are resource limits, not sub-100ms task latency claims.

Enrollment is cached only for observation. If Beads is unavailable, the last
observed roster remains usable with its refresh age and failure visible. A
missing native index can use the last validated transcript path, with discovery
failure still visible. A rejected path never overwrites that fallback. Archived
or moved paths are rediscovered on later sweeps.
A successful enrollment refresh removes tasks absent from the current registry;
it does not delete historical usage. Neither cache authorizes execution.

`watch` is a foreground resident suitable for an independent host supervisor.
It defaults to five seconds between completed batches; `--interval-seconds`
accepts 1–3600. It holds only its own singleton lock, never an admission or
maintenance lock. Every batch launches the canonical launcher against current
local master. A broken new source reports failure; repair takes effect on the
next batch without restarting the resident. No batch overlap is permitted.
The resident transports bounded output and timing, not application policy.
Each output stream retains at most 64 KiB and discards excess with an explicit
truncation flag; it does not spool unbounded logs to disk. Supervisor output
backpressure pauses event delivery with at most one pending event; signal
handling remains active. Shutdown may leave a partial trailing JSON line if the
supervisor is not reading. There is no synchronous final write after shutdown.

SIGINT or SIGTERM stops the timer and drains only its observation child process
group. It cannot stop an executor or Tollgate service. Resident JSON lines report
child failures and source-selected batch results. `telemetry status` reports
registry freshness, oldest/latest task attempts, never-attempted tasks, and the
last 20 task failures. Per-task `usage` reports remaining bytes and parse gaps.
A stale attempt time is observation lag, never evidence that a worker stopped.
Host-service installation is deliberately part of the still-pending cutover.

## Verification and remaining work

Tests cover independent collector processes, repeated records, partial writes,
archive moves, copied files, conflicting usage, oversized records, missing
files, malformed counters, and a transaction failure before cursor persistence.
A fresh-command journey checks that collection survives broken Beads routing
and that a corrupt telemetry store cannot block ordinary task filing or reads.

A disposable September 23 smoke test copied a real 30,779,758-byte transcript
and ingested it in 31 bounded batches. All 732 native response identities and
their input-token sum matched an independent scan. Ten oversized transcript
records remained visible as gaps. This verifies the observed ingestion shape,
not complete coverage, background operation, cost attribution, or task latency.

Real server/CLI tests also exercise round-robin enrolled discovery, native
archive path changes, cached progress during both discovery-provider outages,
wrong native identities, and a corrupt observer database while task filing
continues. A read-only smoke against the installed index found this task by its
exact native ID and ingested 1 MiB into a disposable telemetry store without a
source error; no native database or transcript was changed. A resident test
commits new and broken source while the same timer runs, verifies fresh policy
and visible failure, and stops a blocked child
without leaking it or retaining task locks. Regression cases preserve a
validated fallback through a rejected lookup and subsequent outage, stop with
an unread supervisor pipe, truncate both child streams, and drain a timed-out
child's descendants.

Response-start attribution, model/service-tier evidence, pricing, trace spans,
retention, and per-bead reports remain required. The diagnostic
report exposes their absence and returns no dollar amount. A response's token
count alone cannot establish its bead or role at response start; do not assign
historical usage from the task's current title or current enrollment focus.
