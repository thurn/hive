# Warm command startup and read measurements

On September 23, 2026, removing the second Python startup reduced source-only
command p95 from 112ms to 75ms at eight clients. Complete task reads still took
224ms and ready queries 233ms. **The design's 100ms acceptance target remains
unmet.** Native Beads reads alone exceeded that budget in both runs.

## Reproduce

After `scripts/prepare-check`, run from the checkout being measured:

```sh
PATH="$PWD/.test-tools/bin:$PATH" PYTHONPATH=src:tests \
  .venv/bin/python -P scripts/measure_reads.py --samples-per-client 32
```

The profiler creates a disposable Git repository and private Dolt server. It
seeds 1,000 unfinished beads with native IDs, invokes the canonical `bin/hive`,
and deletes its temporary state on exit. It never uses production Beads state.
`-P` prevents the launcher file in `scripts` from shadowing the `hive` package
inside the development profiler; measured commands use their normal launcher.

Each operation has one warm-up followed by 32 samples per client, at one and
eight concurrent clients. Clients start together and then execute sequentially.
CLI samples include the shell, Python startup, source selection, application
imports, Beads calls, and parsed JSON. Native component samples reuse the
profiler's Python process but start a new `bd` command each time; they are not
whole-command latency. No admission mutations are measured here.

The environment was macOS 26.5.2 ARM64, 18 logical CPUs, Python 3.12.14, Beads
1.2.2, and the pinned Dolt 2.2.0 test binary. Raw artifacts record host load,
source identities, sample counts, errors, and every sample:

- [Baseline](measurements/read-baseline-20260923.json): application and launcher
  source from `cb19325482551a241677ab89cebd60d794994d1f`, with this profiler added
  to a disposable checkout. Its `checkout_commit` identifies that temporary
  commit, not a different application revision.
- [Updated startup](measurements/read-startup-20260923.json): the uncommitted
  startup changes accompanying this document, based on the same commit. The
  recorded dirty flag is intentional. `fixture_commit` identifies each run's
  temporary committed application snapshot.

## Results and limits

All 1,440 timed operations in each run succeeded. Nearest-rank p95 values are
milliseconds; each one-client cell has 32 samples and each eight-client cell
has 256 samples.

| Operation | Baseline, 1 client | Updated, 1 client | Baseline, 8 | Updated, 8 |
| --- | ---: | ---: | ---: | ---: |
| CLI source diagnostic | 85.04 | 56.18 | 112.06 | 74.65 |
| Native task read | 88.03 | 100.97 | 150.73 | 162.20 |
| CLI task show | 182.81 | 154.23 | 265.35 | 223.59 |
| Native active query | 104.08 | 115.73 | 160.61 | 156.13 |
| CLI ready query | 197.42 | 175.49 | 281.11 | 233.09 |

Runs were sequential, updated first, under ordinary host load; they were not a
randomized controlled comparison. Component percentiles are not additive.
The independent reviewer ran short source/MCP checks during the updated run;
the load metadata does not provide per-sample attribution of that interference.
Treat the observed reduction as an engineering profile, not a latency guarantee.

The fixture has no dependency edges, completed history, collector, or backup.
It omits writes, competing claims, and the full scaling matrix. Consequently,
even a passing read number would not establish design acceptance. The retained
tests separately verify delayed imports/assets, guard lifetime, ambient-module
exclusion, and hot reload during a pending MCP wait.

The change retains one isolated standard-library interpreter for a warm call,
delays archive-only imports until a cache miss, and selects warm source with one
local-master read. It does not bypass Beads or introduce a cache of task state.
Further work must address native command cost and measure the complete workload.
