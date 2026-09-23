# Supported Beads boundary

These observations were checked against installed Beads 1.2.2 (`6c124203e`)
and Dolt 2.2.0 on September 22, 2026, using an isolated server and temporary
database. Production `~/brain` was not modified. They guide the adapter; they
are not evidence that Hive's full workflow or latency target is implemented.

- `bd list --json --limit 0` returns hydrated dependency edges and counts.
  Edges contain `issue_id`, `depends_on_id`, and `type`. `bd show` instead
  embeds prerequisite issue details. Hive's list decoder accepts the former
  shape explicitly, checks complete hydration, and rejects unsupported edge
  types rather than quietly treating them as nonblocking.
  Adding `--skip-labels` changes the result to an `issues`/`meta` envelope.
  The process adapter explicitly uses that form and checks its issue count.
- `bd update --claim --metadata ...` is not one atomic lifecycle change.
  The CLI calls `ClaimIssue` and then `UpdateIssue` separately. Hive needs to
  update native status, assignee, and the phase/turn metadata together under
  its shared admission lock, using one ordinary `UpdateIssue` operation.
- `--metadata` shallow-merges metadata keys. Replacing the `hive` subtree
  removes obsolete Hive fields while preserving unrelated metadata keys.
  `--set-metadata hive={...}` stores that object-looking argument as a string;
  it is unsuitable for the structured subtree.
- Creating with `--defer` sets native deferred status in the initial issue
  write. Adding initial dependencies through `--deps` happens after creation.
  A task with prerequisites must start deferred and become eligible only
  after those edges are present. The local lock does not roll back a crash
  between those writes.
- `bd -C` requires an initialized Beads project, even for `bd init`. Setup
  must run initialization with an explicit working directory first.

Primary source at the inspected commit:

- [Update command and metadata merge][update]
- [Creation and post-create dependency writes][create]
- [Native storage update boundary][storage]

[update]: https://github.com/gastownhall/beads/blob/6c124203e/cmd/bd/update.go
[create]: https://github.com/gastownhall/beads/blob/6c124203e/cmd/bd/create.go
[storage]: https://github.com/gastownhall/beads/blob/6c124203e/internal/storage/dolt/issues.go

The codecs reject embedded JSON strings, incomplete dependencies, unknown
phases, mismatched code/artifact states, and native status/ownership conflicts.
Malformed active records must remain visible to the admission service, not
be filtered out and silently removed from its capacity count.

The process adapter now fixes the server endpoint and database explicitly,
discards ambient routing variables, disables server auto-start, and preserves
uncertainty after failed writes. Its real-server tests also check preservation
of non-Hive metadata and resumption after deferred creation. They do not yet
prove concurrent compound admission or recovery of interrupted graph changes.

Still required: guarded compound task operations, interrupted-write and
concurrent-claim integration tests, and end-to-end measurements. These
observations do not justify direct Dolt access by themselves.
