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

Ordinary task operations do not import or call this collector. Collection and
reporting do not open Beads or retain the task maintenance guard. SQLite lock
waiting is bounded to 100ms; a failed collection can be retried separately.
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

This is the incremental ingestion boundary of the full collector. Automatic
registered-task discovery, archive-path discovery, the independent background
transport, response-start attribution, model/service-tier evidence, pricing,
trace spans, retention, and per-bead reports remain required. The diagnostic
report exposes their absence and returns no dollar amount. A response's token
count alone cannot establish its bead or role at response start; do not assign
historical usage from the task's current title or current enrollment focus.
