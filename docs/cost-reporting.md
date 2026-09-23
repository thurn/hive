# API-equivalent cost estimates

`hive cost --task <native-task-id>` estimates observed model usage at Standard
API rates. `--tier fast`, `--tier batch`, and `--tier flex` select another pricing
assumption. This is a comparison estimate, not subscription billing. The command
reads derived telemetry and works while Beads is unavailable.

The [official pricing page][prices], model pages for [Astra][astra], [Sol][sol],
and [Luna][luna], and [cache accounting][cache] were checked on September 23,
2026. The initial catalog covers their exact `gpt-6-*` model IDs. Unknown models
and aliases remain unpriced rather than borrowing a similar model's rates.
Ordinary catalog edits follow local master; no network request runs in a report.

[prices]: https://developers.openai.com/api/docs/pricing
[astra]: https://developers.openai.com/api/docs/models/gpt-6-astra
[sol]: https://developers.openai.com/api/docs/models/gpt-6-sol
[luna]: https://developers.openai.com/api/docs/models/gpt-6-luna
[cache]: https://developers.openai.com/api/docs/guides/prompt-caching

## Evidence and limits

The installed transcript provides exact per-response token usage and native
response/task/turn IDs. Its `turn_context` records expose a configured model for
a native turn. The installed app-server schema also permits model changes during
an active turn. Therefore a turn context is not proof of the actual upstream
model for every response. Reports name this assumption explicitly.

The collector persists model observations in the same transaction as usage and
its cursor. Conflicting model observations for one turn invalidate that turn's
estimates; replay cannot pick the last model or clear the conflict. Missing
context remains unknown. The collector does not read the current task model,
name, or enrollment to assign historical model use.

Native response-start identity and actual service-tier evidence are not yet
available through this ingestion boundary. `observed_service_tier` remains null;
`pricing_tier_assumption` records the requested comparison tier. Regional
processing uplifts and tool fees are excluded. All responses remain explicitly
unattributed to beads and roles; a model assumption cannot establish attribution.

## Arithmetic and retained evidence

Every input token belongs to one pricing category. Reasoning output is already
included in output tokens and is not charged a second time.

```text
ordinary input = input - cached input - cache-write input
cost = ordinary input × input rate + cached input × cache rate
     + cache-write input × write rate + output × output rate
```

The decoder rejects overlapping input counters. Price arithmetic uses integers
in trillionths of a dollar, so large accumulated totals cannot overflow SQLite
integer arithmetic or lose precision through floating-point rounding. JSON USD
amounts are exact decimal strings; apparent precision does not remove the model
and tier assumptions.

For the supported models, requests above 272,000 input tokens use doubled input
and cache rates and 1.5 times output rates for the entire request. The boundary
includes cached input. Fast rates are twice Standard; Batch and Flex are half
Standard. Each persisted response estimate includes its exact applied rates,
model, assumed tier, context band, source URL, and price-observation date.

For example, Astra Standard with 1,000 input tokens, 400 cache reads, 200 cache
writes, and 50 output tokens estimates USD 0.0094. Reasoning tokens within those
50 output tokens add no separate charge.

The first successful estimate for a response and comparison tier retains its
rate evidence. Later catalog edits price newly estimated responses, without
silently repricing older ones. A report can contain several rate groups and
shows each group's subtotal and response count. Conflicting model context still
invalidates previously cached estimates for that turn.

## Coverage and replay

The report distinguishes missing usage, missing model context, conflicting model
context, and unknown prices. `observed_estimate_usd` is present only when every
observed response can be priced under the stated assumptions. A priced subset
can still have a subtotal when other responses are unknown. With no priced
responses, both amounts remain null; observed zero usage can produce zero.

Every report includes source freshness, unread bytes, incomplete-tail status,
parse gaps, and source errors. A priced observed subtotal is never evidence of
complete transcript coverage. Historical estimates remain readable during a
source outage, with the outage visible. The rate-cache writes affect only the
derived telemetry store, never task ownership or execution.

Older collected responses may lack model context because that data was not
previously ingested. Explicitly replay the source to collect its model records:

```sh
hive telemetry collect --task <id> --transcript <path> --from-start
```

Replay reads only the normal bounded chunk from the start, then ordinary
collection resumes from that cursor. Native response IDs prevent recounting;
existing estimates retain their rate evidence. A failed source identity check
does not reset the previous cursor. No task state or native transcript changes.

## Validation and remaining acceptance

Behavioral tests cover cache partitions, reasoning output, the exact long-context
boundary, all supported comparison tiers, unknown prices, missing usage, observed
zero, context conflicts, and totals spanning multiple database read batches.
A real CLI/server journey replays missing context, commits a new catalog while
retaining the same observation store, preserves old estimates, prices a new
response under the new rates, and reports during a source and Beads outage.
An interrupted ingestion rolls back both context and response progress.

A disposable native smoke copied a 34,501,158-byte transcript and ingested it in
34 bounded chunks. All 850 observed response estimates matched an independent
decimal calculation. Eleven oversized-record gaps remained visible. Native
files were unchanged; this establishes the observed ingestion/calculation path,
not actual billing or complete response-start attribution.

Actual response-start attribution, observed upstream model/tier evidence,
per-bead and project/role reports, cold-review aggregation, trace spans, and
retention remain required by the Hive design. This command does not establish
those broader acceptance requirements or the task-operation latency target.
