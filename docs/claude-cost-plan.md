# Plan: API-equivalent USD cost for Claude Code threads

- Beads: design `hv-67e`. Implementation is deferred until the user approves: `hv-b4r` (steps 1–5 and 8), `hv-2uo` (steps 6–7), `hv-qg7` (steps 9–10), `hv-8ib` (step 11).
- Status: draft revision 6, 2026-09-23. Revision 2 added a per-request event source for requests the transcript omits (§5.7) and a request-level detail model for a future bead spend UI (§5.8). Revision 3 addresses the second cold review (§12). Revision 4 brings the per-bead breakdown into scope (§5.9). Revision 5 moved ownership to the Beads events table after the third cold review. Revision 6 fixes its cursor and adds per-event checks after the fourth review.
- Repository: `/Users/dthurn/hive`.
- Related code: `src/hive/usage.py`, `src/hive/pricing.py`, `src/hive/cost_report.py`, `src/hive/usage_store.py`, `src/hive/collection.py`, `src/hive/native_transcripts.py`, `src/hive/thread_links.py`.
- Related docs: `docs/invariants.md`, `docs/implementation.md` (it says "Telemetry and cost read only Codex transcripts and prices").
- Rate source: <https://platform.claude.com/docs/en/about-claude/pricing>, checked 2026-09-23.

## 1. Summary

Hive estimates the cost of Codex threads today. It reads Codex's native transcript, stores each API response's token counts, and prices each response with a checked-in rate card. Claude Code sessions are already linked to beads, but they are never collected or priced.

This design adds Claude Code in three phases:

1. **Thread parity (exact arithmetic).** Discover Claude transcripts, decode each API request's usage, and price it from the published Claude rates. Subagent transcripts are included in their parent thread's total.
2. **Subagent and skill breakdown (exact).** Split the thread total by subagent and by the skill that was active. Both are exact partitions of priced requests.
3. **Tool attribution (estimated).** Allocate each request's cost to the tool calls whose inputs and results make up the prompt. Token positions are measured; splits within one step are estimated from byte sizes. The report labels this as an allocation.

Phase 1 also keeps **one detail row per API request**, not just totals (§5.8). A **per-bead breakdown** then assigns each request to the bead its thread owned at that moment, using the ownership history Beads keeps (§5.9). This works for both Codex and Claude threads. An optional second source fills in the requests that transcripts omit: Hive receives Claude Code's own OpenTelemetry `api_request` events on a localhost endpoint (§5.7).

**The premise needs one correction.** The Codex path today is *thread-level only*. It has no tool-call or subagent attribution. So "the same manner as Codex" covers Phase 1. Phases 2 and 3 are new for both hosts. Phase 3's engine is host-neutral, so Codex can use it later (§11).

**A coverage finding changes what the report can claim.** Claude Code makes some API requests that its transcript never records. In 6 of 7 local sessions checked, the transcript-derived estimate was 3–25% below Claude Code's own session cost figure (§4.6). Without the event source, the report shows this gap rather than hiding it. With the event source enabled, each missing request becomes its own detail row.

## 2. Goals and non-goals

Goals:

- Price each recorded Claude API request from exact per-request token counts and the published rates. Keep the rate evidence first used for each request, as Codex does.
- Report a per-thread total for bead-linked Claude sessions through the existing `hive cost --task` and collector commands.
- Include subagent work in its parent thread. Break it down by subagent.
- Offer tool-level attribution that adds up exactly to the priced total, with every estimated step labelled.
- Keep every coverage gap visible: unrecorded requests, unknown models, unknown modifiers, parse gaps and partial streams.
- Keep a detail row for every observed API request, with the dimensions a future bead-level spend UI needs: time, model, token categories, priced amounts, subagent, skill, subsystem and tool (§5.8).
- Report each bead's spend across every thread that owned it, on either host (§5.9). Claude rows add agent, skill and query-source breakdowns; Codex rows have none.

Non-goals:

- Billing truth. The estimate is API-equivalent. Subscription plans, negotiated discounts and Bedrock or Vertex pricing are out of scope.
- The UI itself. This design provides the data and a `hive cost --bead` command for a UI to use.
- Allocating spend by creator links or by guessing intent. Only ownership intervals from the Beads events table decide which bead a request is charged to. The one allocation rule is an equal split when a thread held several beads at once (§5.9). This changes two invariants explicitly.
- Changing any task, bead or Claude Code setting. Collection stays read-only. The event source needs the operator to add Claude Code settings by hand (§5.7). Hive documents those settings but never writes them.
- Phase 3 for Codex. The engine is designed so Codex can adopt it, but that is a follow-up.

## 3. What the current Codex path does

The Claude path should copy these properties:

- **Unit of cost:** one `token_usage_record` per `response_id`. Cumulative `token_count` events are ignored (`usage.py`).
- **Model:** taken from `turn_context` for the turn. A turn with conflicting models is left unpriced (`turn_model.py`).
- **Tier:** a CLI assumption (`--tier standard|fast|batch|flex`). Codex does not record the tier that was used.
- **Arithmetic:** exact integers in picodollars. A rate in $/MTok equals 10⁶ picodollars per token (`pricing.py`).
- **Evidence:** a report reads without the write lock, then stores new quotes in `response_estimates` on a best-effort basis. Once stored, a quote is reused, so later edits to the rate card do not reprice that response. Under contention, storing is deferred, and the report counts those quotes as `unretained_estimates` (`cost_report.py`).
- **Coverage:** counts of priced and unpriced responses, with reasons, parse gaps, source errors and a coverage sentence.
- **Collection:** bounded 1 MiB incremental reads with byte cursors. A transcript that is replaced or truncated is recorded as a gap (`usage_store.py`).
- **Discovery:** thread IDs come from bead creator and assignee fields. Transcript paths come from Codex's `state_5.sqlite` index (`native_transcripts.py`).

Claude session IDs pass `thread_links.thread_id()`, which accepts any canonical UUID. Since `bec3185`, each link also has a `collected` flag from `codex_thread()`, which is true only for UUIDv7 IDs. So Claude's UUIDv4 sessions are linked with `collected: false`, never swept, and counted as `uncollected_threads`, and `hive cost` reports `usage_collectable: false` with a `collection_gap`. Phase 1 replaces that version rule with the two-host probe in §5.2. The probe also fixes the rule's known gap: a Codex thread whose ID is not UUIDv7 is silently left uncollected.

## 4. Claude Code transcript facts

All facts below were observed in local transcripts from Claude Code 2.1.181–2.1.281. That is 27 files and 1,133 API messages. The collector must re-check these facts on every read (§5.3), because Claude Code does not document its transcript format.

### 4.1 Layout

```text
~/.claude/projects/<cwd-slug>/<session-id>.jsonl                     # main thread
~/.claude/projects/<cwd-slug>/<session-id>/subagents/agent-<id>.jsonl # one per subagent
~/.claude/projects/<cwd-slug>/<session-id>/subagents/agent-<id>.meta.json
~/.claude/projects/<cwd-slug>/<session-id>/tool-results/*.txt        # large outputs stored outside the context
```

- `<session-id>` equals `CLAUDE_CODE_SESSION_ID`. That is the ID the skills store as `hive_origin_thread` and as the assignee.
- `<cwd-slug>` is derived from the launch directory. Hive should not try to rebuild it. Discovery globs `~/.claude/projects/*/<session-id>.jsonl` and requires exactly one match.
- Every record in a subagent file has `"isSidechain": true`, an `agentId`, and the **parent's** `sessionId`.
- The `meta.json` file links the subagent to its spawning call:

```json
{"agentType":"general-purpose","description":"Cold warden review of hv-4up",
 "toolUseId":"toolu_013uwAHwDLTzpR6FK2rER36X","spawnDepth":1}
```

### 4.2 One API request is written as several lines

Claude Code writes one `assistant` line per content block. Every line repeats `message.id`, `requestId` and `message.usage`:

```text
{"type":"assistant","requestId":"req_011CfMXtHT…","message":{"id":"msg_011CfMXtL3…","model":"claude-opus-5-5",
  "content":[{"type":"thinking",…}],"usage":{…,"output_tokens":155}}}
{"type":"assistant","requestId":"req_011CfMXtHT…","message":{"id":"msg_011CfMXtL3…",
  "content":[{"type":"tool_use",…}],"usage":{…,"output_tokens":155}}}
```

- In 95 of 1,133 messages, earlier lines carry a **partial** `output_tokens` value (for example 1, 1, 362). Only the last line has the final count. Input and cache fields never differed between lines.
- The rule is: group lines by `requestId`, require the input and cache fields to be equal, and keep the maximum `output_tokens`. An increase is a normal update, not a conflict. This differs from the Codex rule, which treats any usage change as a conflict.
- If a stream is cut off, only the partial count exists. The collector cannot tell a partial count from a final one without a later line. So the report counts responses whose last line has `stop_reason: null` as `possibly_partial_output`. They are still priced. In the sample, every line with a partial count had `stop_reason: null` and every final line after a partial had a non-null value. 42 single-count responses ended with `stop_reason: null`, so this counter will usually be nonzero; it is an upper bound on truncation, not a count of truncated streams.
- Two lines had `model: "<synthetic>"` and zero usage. These are local placeholders, not API calls, and are skipped.

### 4.3 Usage fields

```json
"usage": {
  "input_tokens": 2,
  "cache_creation_input_tokens": 13089,
  "cache_read_input_tokens": 41519,
  "output_tokens": 155,
  "output_tokens_details": {"thinking_tokens": 22},
  "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 13089},
  "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
  "service_tier": "standard", "speed": "standard", "inference_geo": "not_available",
  "iterations": [{"type": "message", "input_tokens": 2, …}]
}
```

Differences from Codex:

- `input_tokens` counts **uncached** tokens only. The prompt size is `input + cache_creation + cache_read`. Codex's `input_tokens` includes cached tokens.
- Cache writes come in two TTL classes with different prices (5 minutes and 1 hour). Claude Code mostly writes 1-hour entries.
- `speed`, `service_tier` and `inference_geo` are **observed per request**. Claude needs no `--tier` guess.
- The model is on each request (`message.model`). Claude needs no turn-context join and has no model conflict.
- `iterations` held exactly one `message` entry in every observed record. It was absent in older versions. Server-side fallbacks can add iterations served by other models.

### 4.4 Identity and duplication

- `requestId` (`req_…`) is the response identity. `message.id` is used only when `requestId` is absent. Two old records lacked it.
- Forks, resumes and compaction may copy history into another file. The same identity can therefore appear in two files. Identities are globally unique, so the store keeps one row for each. A second copy with identical usage is ignored. A copy with different input or cache fields is a conflict gap.

### 4.5 Prompt growth is append-only in practice

Across 1,121 consecutive request pairs in the sample, the prompt size never shrank. Only 3 transitions read less than half the previous prompt from cache, which suggests a cache expiry. No compaction boundary appeared in the sample. Phase 3 relies on this property and detects where it fails (§7.5).

### 4.6 Unrecorded requests: a coverage gap Codex does not have

Claude Code writes a `cost-state` record holding its own running total for the process:

```json
{"type":"cost-state","totalCostUSD":2.0342942,"modelUsage":{"claude-opus-5-5":
  {"inputTokens":1098,"cacheCreationInputTokens":137509,"cacheReadInputTokens":3621216,"outputTokens":20421,"costUSD":2.0342942}}}
```

Transcript-derived totals (main plus subagents, published rates) compared with the last `cost-state`:

| Session | Subagents | Transcript estimate | Last `cost-state` | Gap |
|---|---|---|---|---|
| `4dd72542` | 1 | $0.89054 | $0.8905368 | 0% (same token counts) |
| `1adb320e` | 1 | $1.96510 | $2.0342942 | 3.4% |
| `864272d4` | 1 | $2.17344 | $2.3113208 | 6.0% |
| `f8e5bf9f` | 2 | $2.19883 | $2.4480864 | 10.2% |
| `315785c5` | 0 | $1.81034 | $2.0184174 | 10.3% |
| `10ff4d2f` | 0 | $0.44416 | $0.5348362 | 17.0% |
| `26371313` | 0 | $0.29801 | $0.3977570 | 25.1% |

The exact match shows that the published rates, the multi-line grouping and the subagent inclusion agree with Claude Code's own price table. It is not an independent check against Anthropic's billing. (An earlier draft of this table dropped one of `f8e5bf9f`'s two subagents. Acceptance now checks that every `agent-*.jsonl` file is collected; see §10.)

The gaps come from requests that are missing from the transcript. `cost-state` shows extra uncached input (for example 1,098 against 86 tokens) and extra cache reads. Side requests to other models are confirmed: `26371313`'s `modelUsage` includes `claude-haiku-4-5-20251001`, and no Haiku request appears in that transcript.

Consequences:

- A transcript total is a **lower bound** for Claude Code. The report says so.
- The report shows the latest `cost-state` total as `host_reported`, and the difference as `unrecorded_usd_lower_bound`, when they can be compared (§5.6). Neither value is attributed to a bead, a subagent or a tool.
- `cost-state` is a running total per process, keyed by `startTime`. It is also written partway through a process: `26371313` has $0.122 and later $0.398 with the same `startTime`. Its behaviour across resumes is unverified. Hive never adds `cost-state` values together. §5.6 gives the exact comparison rule. Acceptance must settle the behaviour across resumes (§10).
- A complete per-request source exists: Claude Code's OpenTelemetry `api_request` events, which cover side requests too. §5.7 adds it as an optional source.

## 5. Phase 1: thread parity

### 5.1 Host-neutral identity

The current types are named after Codex (`CodexTaskId`, `CodexTurnId`, `Owner`). Add a host dimension instead of copying each type:

```python
class Host(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"

ThreadId = NewType("ThreadId", str)          # replaces CodexTaskId in observation code

@dataclass(frozen=True)
class Owner:
    host: Host
    thread: ThreadId
    agent: AgentId | None     # Claude subagent ID; None for the main thread
    turn: TurnId | None       # Codex turn; Claude promptId when present
```

`CodexTaskId` stays as an alias during migration so that the change stays reviewable. Response identity becomes `(host, response)` so the two hosts' identity spaces cannot collide.

### 5.2 Discovery

A thread ID from a bead does not say which host produced it. Codex IDs happen to be UUIDv7 and Claude IDs UUIDv4, but that is not a contract, and `codex_thread()` currently depends on it. Discovery therefore probes both sources and replaces `codex_thread()`. `collected` becomes true when exactly one host has a transcript:

1. Look up the Codex index (existing code).
2. Look for `~/.claude/projects/*/<id>.jsonl`. The glob is bounded to one directory level. Symlinks and non-regular files are rejected, as today.
3. Exactly one host match: record `host` in `collection_tasks` and cache the path, as `validated_path` is cached today.
4. Matches on both hosts: record a gap and collect neither.
5. No match: keep today's "no transcript" error.
6. The Codex index errors (`native_error`): the both-hosts check cannot be decided. Use the host already cached in `collection_tasks`. If none is cached, record a gap for this pass and do not treat the thread as Claude-only.

The Claude projects root is a new optional bootstrap setting, `claude_projects`, which defaults to `~/.claude/projects`. Tests point it at a fixture directory. `CLAUDE_CONFIG_DIR` is honoured only through this explicit setting, never from the ambient environment. This matches the explicit-routing rule for Beads.

Subagent files are listed from `<session>/subagents/agent-*.jsonl` on every collection pass. New subagent files can appear at any time.

**File identity check.** Codex's collector requires line 1 to be a `session_meta` record and checks it against the thread (`usage_store.py`). Claude files start with `queue-operation`, `user`, `custom-title` or `last-prompt` records, so that check would reject every Claude file. The Claude equivalent:

- The file name must be `<thread>.jsonl` for the main file, or `agent-<id>.jsonl` under `<thread>/subagents/`.
- The first record that carries `sessionId` must equal the thread. Until such a record has been read, the file's cursor does not advance past the records before it.
- For subagent files, the first record that carries `agentId` must equal `<id>`.

A mismatch is a source error for that file, as a wrong `session_meta` is for Codex.

**Manual commands.** `hive telemetry collect --task ID --transcript PATH` accepts one Claude file, main or subagent. It checks identity as above and uses that file's own cursor. `hive telemetry usage` gains `host` and `by_agent` fields. For Claude its token totals follow the normalized meaning in §5.3, where `input` includes cached tokens.

### 5.3 Decoding

A new `claude_usage.decode(record, thread) -> ClaudeResponse | None` function is the only boundary that trusts the transcript shape. It returns `None` for non-`assistant` records. It raises `INVALID_RECORD`, which becomes a parse gap, when:

- `sessionId` differs from the thread. This matches the Codex "Usage is for another native task" check.
- A record in a subagent file lacks `isSidechain: true` or has an `agentId` different from the file name.
- A usage counter is missing, negative, a boolean, or over 2⁶³−1.
- The 5-minute and 1-hour cache writes do not add up to `cache_creation_input_tokens`. Older records have no `cache_creation` object. Their writes are priced as 5-minute writes and marked `ttl_assumed` in the report.
- `iterations` contains a type other than `message`, or its totals disagree with the top level. The request is **stored but unpriced**, with reason `unsupported_iteration`. It is not dropped.

Normalized tokens widen the shared `Tokens` type. Codex fills the new fields with zero:

```python
@dataclass(frozen=True)
class Tokens:
    input: int                 # full prompt size, including cached tokens (Codex meaning)
    cached_input: int          # cache reads
    cache_write_input: int     # 5-minute writes (Codex: all cache writes)
    cache_write_1h_input: int  # new; Codex always 0
    output: int
    reasoning_output: int      # Claude: output_tokens_details.thinking_tokens when present

# Claude: input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

Add request modifiers, observed per response and stored with it:

```python
@dataclass(frozen=True)
class Modifiers:
    speed: str | None          # "standard" | "fast"
    service_tier: str | None   # "standard" | "priority" | "batch" | …
    inference_geo: str | None  # "not_available" | "global" | "us" | …
    web_searches: int
```

### 5.4 Storage changes (`telemetry.sqlite3`)

The store is derived and disposable. Operations already allow deleting it (`docs/operations.md`). Still, a schema migration that keeps existing Codex rows is cheap and preserves historical price evidence:

- `sources`: the primary key becomes `(task, file)`, where `file` is `""` for the main transcript or `agent-<id>`. Each file keeps its own cursor, device/inode and gap history.
- `responses`: keep the single global `response` primary key. Claude `req_…` IDs cannot collide with Codex `resp_…` IDs, and a collision would be caught as a conflict. Add `host`, `agent`, `model`, `modifier_key`, `modifiers` (JSON), `cache_write_1h`, `complete` and `skill`. Make `turn` nullable; Claude stores `promptId` when present. Existing rows get `host='codex'` and `cache_write_1h=0`.
- `gaps`: add `file`.
- `collection_tasks`: add `host`.
- New table `claude_agents(task, agent, tool_use_id, agent_type, description, spawn_depth)`, filled from `meta.json`.
- With the event source (§5.7): `claude_request_events` (one row per request identity, allow-listed attributes, `unjoinable` flag), `claude_request_errors` (counts per session), `claude_event_sequences(session, process, first_seen, max_sequence, missing)` and `otlp_ingest_gaps`.
- `response_estimates` is unchanged. The key already includes the tier. For Claude, the "tier" key is the canonical modifier string, for example `standard/standard/global`.

**Migration mechanics.**

- `connect(write=…)` currently runs `executescript` before any transaction, and `executescript` commits on its own. The migration therefore runs in its **own** `BEGIN IMMEDIATE` transaction, before any `CREATE TABLE IF NOT EXISTS`, guarded by `PRAGMA user_version`. It runs only on write connections. A read connection (`write=False`, used by reports) that finds an older `user_version` returns a structured "store not yet migrated" error. The next collector write then migrates the store. Contention during migration is reported as the existing `Busy` error.
- Changing the `sources` primary key needs a SQLite table rebuild (create new, copy, drop, rename) inside that transaction.
- The migration waits longer for the write lock than the usual 0.1 seconds (5 seconds). If it cannot get the lock, the call fails and changes nothing; the next call retries.
- From step 1 on, every `INSERT` names its columns. Today's positional `INSERT … VALUES (?, …)` and `ON CONFLICT(task)` break against a migrated table.

**Stored usage JSON.** `save()` compares stored `usage` JSON as a string, and `tokens()` requires every key. Adding `cache_write_1h_input_tokens` would make every re-read Codex response a "conflicting usage" error, and old rows would fail to parse. Two changes fix this:

- `save()` compares parsed `Tokens` values, not strings.
- `tokens()` treats a missing `cache_write_1h_input_tokens` as 0.

The migration also rewrites stored `usage` JSON into the new form, so the data is uniform.

**Transition window.** The deployed code has no `user_version` guard. A `hive cost` call or sweep that is still running the previous commit can open the store after the migration and fail on the new schema. Such a failure rolls back and loses no data. The next call selects the new source and succeeds. This is a transient error, not corruption. Acceptance covers it (§10, item 7). After step 1 lands, a `user_version` newer than the code's version raises a clear `INVALID_RECORD`, which protects later migrations and rollbacks.

**Upsert rule for Claude responses:** input and cache fields must match. `output_tokens` and `complete` may only move forward. A decrease is a conflict gap.

### 5.5 Pricing

Rates are **explicit per model**, not base price × multiplier. The cache-read multiplier varies by model: 0.05× on Opus 5.5, 0.025× on Fable 5.1, 0.1× elsewhere. All values below are $/MTok, stored as picodollars per token:

| Model ID | Input | 5m write | 1h write | Cache read | Output |
|---|---|---|---|---|---|
| `claude-fable-5-1` | 10 | 12.50 | 20 | 0.25 | 50 |
| `claude-fable-5` | 10 | 12.50 | 20 | 1 | 50 |
| `claude-opus-5-5` | 4 | 5 | 8 | 0.20 | 20 |
| `claude-opus-5` | 5 | 6.25 | 10 | 0.50 | 25 |
| `claude-opus-4-8` | 5 | 6.25 | 10 | 0.50 | 25 |
| `claude-opus-4-7`, `-4-6` | 5 | 6.25 | 10 | 0.50 | 25 |
| `claude-sonnet-5` | 2 | 2.50 | 4 | 0.20 | 10 |
| `claude-sonnet-4-6` | 3 | 3.75 | 6 | 0.30 | 15 |
| `claude-haiku-4-5` | 1 | 1.25 | 2 | 0.10 | 5 |

Modifiers, from the same page:

- **Fast** (`speed: "fast"`): Opus 5.5 input/output 8/40, Opus 5 and 4.8 10/50. Cache multipliers apply on top of the fast input rate. For Opus 5.5 fast that gives 10 / 16 / 0.40 for 5m write / 1h write / read. Fast on any other model: unpriced, `unknown_modifier`.
- **US inference** (`inference_geo: "us"`, Claude 4.6 and later): ×1.1 on every category, after fast. `global` and `not_available` mean ×1.0. Any other value: unpriced.
- **Service tier:** only `standard` is priced. `priority`, `batch` and others are unpriced, reason `unknown_service_tier`. Claude Code does not use Batch.
- **Long context:** Claude 4.6 and later have no surcharge up to 1M tokens. For the models listed, no long-context rule is needed, and the report records `long_context: false`. An unlisted model is unpriced anyway.
- **Web search:** $10 per 1,000 = 10¹⁰ picodollars per search, from `server_tool_use.web_search_requests`. It is a separate `server_tool_fees` line, included in the total. Web fetch costs nothing extra.
- **Exact model IDs only.** Dated snapshots, `[1m]` suffixes, Bedrock IDs and unknown models stay unpriced, as for Codex.

All products are exact integers. For example, 1.1 × 200,000 = 220,000 picodollars.

**Quote identity.** Today `Quote.tier` is the `PricingTier` enum, `estimate()` compares it with the requested tier, and the report joins `response_estimates` on one tier for the whole query (`e.tier=?`). Claude responses each carry their own modifiers, so:

- `Quote` gains `host` and a `modifier_key` string. For Codex, `modifier_key` is the `PricingTier` value, so existing rows read unchanged. For Claude it is the canonical `speed/service_tier/inference_geo` string.
- `Quote.read` validates `modifier_key` according to `host`. A missing `host` means `codex`.
- `response_estimates` keeps its `(response, tier)` key, with `tier` holding the `modifier_key`. The report joins Codex rows on the requested `--tier`, and Claude rows on the response's own stored `modifier_key`.
- Step 3 includes a round-trip test: a Claude quote is written, read back through `Quote.read`, and still used after a rate-card edit.

The `Quote` evidence JSON gains optional keys. It still reads existing Codex quotes unchanged:

```json
{"host":"claude","model":"claude-opus-5-5",
 "modifier_key":"standard/standard/not_available","long_context":false,
 "modifiers":{"speed":"standard","service_tier":"standard","inference_geo":"not_available"},
 "rates_picos_per_token":{"input":4000000,"cached":200000,"cache_write":5000000,
                          "cache_write_1h":8000000,"output":20000000},
 "server_tool_fees":{"web_search_picos":10000000000},
 "price_observed":"2026-09-23",
 "source":"https://platform.claude.com/docs/en/about-claude/pricing","usd":"0.001234000000"}
```

Amount formula for Claude:

```text
amount = input_tokens·r_in + cw5·r_w5 + cw1h·r_w1h + cache_read·r_read + output·r_out
       + web_searches·fee_search
```

Here `input_tokens` is the uncached count, which is `Tokens.input − cached − cw5 − cw1h`, the same derivation Codex uses today.

### 5.6 The `hive cost` report for a Claude thread

The report keeps the current field names, so existing consumers still work, and adds:

```json
{
  "code": "ApiEquivalentCost",
  "host": "claude",
  "observed_responses": 214, "priced_responses": 214,
  "possibly_partial_output": 0,
  "unknown_model_price": 0, "unknown_modifier": 0, "unsupported_iteration": 0,
  "priced_subset_usd": "1.965100000000",
  "observed_estimate_usd": "1.965100000000",
  "server_tool_fees_usd": "0.000000000000",
  "pricing_tier_assumption": null,
  "observed_modifiers": [{"speed":"standard","service_tier":"standard","inference_geo":"not_available","responses":214}],
  "host_reported": {"usd": "2.0342942", "source": "cost-state", "scope": "latest process only", "observed": "…"},
  "unrecorded_usd_lower_bound": "0.069194200000",
  "coverage": "API-equivalent estimate from recorded requests; Claude Code makes requests its transcript omits, so this is a lower bound. …"
}
```

- `observed_estimate_usd` keeps its current meaning: a value only when every observed response is priced. It does not claim completeness against unrecorded requests. `coverage` states that.
- **Named totals.** `priced_subset_usd` covers transcript requests (`source` = `both` or `transcript`). `event_only_usd` covers `events`-only rows. `by_agent`, `by_skill` and `by_tool` partition `priced_subset_usd`. Each of them also carries an `event_only` row that equals `event_only_usd`, grouped by `query_source`, which is not attributed to a subagent or tool. `observed_responses` counts transcript requests only; `event_only_requests` is separate.
- `--tier` is rejected for Claude threads with `INVALID_INPUT`, because Claude reports its modifiers per request. Silently ignoring the flag would mislead. The argparse default changes from `"standard"` to `None`, so an explicit flag can be told apart from the default. Codex still uses `standard` when the flag is absent.
- `host_reported` and `unrecorded_usd_lower_bound` are filled only when **all** of these hold. Otherwise both are `null`, with a `host_reported_reason`:
  - exactly one distinct `startTime` appears among the main file's `cost-state` records;
  - the last `cost-state` record comes after the last priced request in every file of the thread, compared by timestamp;
  - its `startTime` is at or before the first request's timestamp;
  - the difference is not negative.
- `host_reported.has_unknown_model_cost` copies `hasUnknownModelCost`. When it is true, the host total is itself incomplete, and the report says so.

### 5.7 Claude Code request events (optional second source)

Transcripts omit some requests (§4.6), and `cost-state` gives only totals. A future spend UI needs each request, so Hive can also receive Claude Code's documented OpenTelemetry **`claude_code.api_request`** log events. The facts below come from <https://code.claude.com/docs/en/monitoring-usage>.

**What an `api_request` event carries:**

- One event for each API request, including side requests.
- Identity and timing: `session.id`, `prompt.id`, `event.timestamp`, `event.sequence`, `request_id` (`req_…`, present only when the API returned one) and `client_request_id`.
- Usage and cost: `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `cost_usd_micros`, `duration_ms`, `speed` (`"fast"` or `"normal"`) and `effort`.
- Attribution:
  - `query_source` names the subsystem, such as `repl_main_thread`, `compact` or a subagent name.
  - `agent.name`, `skill.name`, `plugin.name`, `mcp_server.name` and `mcp_tool.name` name the agent, skill, plugin or MCP tool behind the request.
  - User-defined agent names and user-configured MCP server and tool names are redacted to `custom`. Third-party plugin skill names become `third-party`. The transcript keeps the real names, so transcript values win whenever both sources have the request.
- **Not in the event:** the 5-minute and 1-hour cache-write split, `inference_geo`, `service_tier`, and web search counts.
- **Ordering:** `event.sequence` is a 0-based counter for each Claude Code process, across all event types. Resuming a session starts a new process with a new counter. `/clear` assigns a new `session.id`.
- **Join key:** `request_id` matches the transcript's `requestId`. The docs call such transcript joins version-specific, not a stable contract. So Hive checks the join on every Claude Code version it sees (see "Coverage").

**Operator configuration.** Hive never edits Claude Code settings. The operator adds these entries to `~/.claude/settings.json`. `hive telemetry otlp-config` prints them with a placeholder for the secret, and writes the real secret only to a file the operator chooses. That way an agent running the command does not copy the secret into a transcript.

```json
{"env": {
  "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
  "OTEL_LOGS_EXPORTER": "otlp",
  "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
  "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://127.0.0.1:<port>/v1/logs",
  "OTEL_EXPORTER_OTLP_LOGS_HEADERS": "Authorization=Bearer <secret>",
  "OTEL_METRICS_INCLUDE_VERSION": "true"
}}
```

- The headers go in the per-signal logs variable, so the secret is never sent to a metrics or traces endpoint the operator enables later.
- The metrics exporter is left alone. Hive does not set `OTEL_METRICS_EXPORTER`, so an operator's existing metrics setup is not overridden.
- `OTEL_METRICS_INCLUDE_VERSION=true` adds `app.version` to events. It is off by default.
- Content logging stays off: `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT` and `OTEL_LOG_RAW_API_BODIES`. Hive needs no prompt or tool content from events.
- Managed settings can remove OTLP variables that a user set. Acceptance records whether that happened.
- The settings affect only Claude Code processes started afterwards. Earlier sessions stay transcript-only.
- Nothing leaves the machine; the endpoint is loopback only.

**Receiver: a small resident listener, with parsing in reloadable code.** The invariants allow the resident observer to keep only timer and connection continuity. Ordinary source edits must not need a restart. So the listener does as little as possible, and the design changes that invariant explicitly to allow a loopback log listener (§9, step 6):

- `hive telemetry watch --otlp-port <port>` adds an asyncio HTTP listener bound to `127.0.0.1`, in the same resident process as the timer. Without the flag, nothing listens. The default port is **4319**, not the standard 4318, so it does not clash with a local OpenTelemetry collector.
- If the port cannot be bound, the timer keeps running and the failure appears in collector status.
- The listener accepts only `POST /v1/logs` with `Content-Type: application/json` and a loopback `Host` header. It checks the bearer secret with a constant-time comparison.
  - It rejects anything else (401, 404, 405, 413 or 415), including `OPTIONS` preflight.
  - Bodies are at most 4 MiB, with at most 8 open connections. Headers must arrive within 5 seconds and the body within 30.
  - A slow or hostile client therefore cannot stall the timer.
- Each accepted body is written unchanged to `${state}/otlp-spool/<ns>-<n>.json`: first to a `.tmp` file, then renamed. The directory is 0700 and files are 0600. The listener replies `200 {}` and does no parsing.
- If the spool exceeds 256 MiB, the listener replies 503 and appends the time and count to `otlp-spool/rejections.log`. This is the only state the listener keeps.
- The secret is 32 random bytes in `${state}/otlp-secret` (0600).
  - It stops local processes from posting events, whether by accident or on purpose.
  - It does not protect against a process that binds the port while Hive is down. That process would receive the secret and event bodies, including the user's email. This risk is accepted: Hive runs as a single local user.
- Changes to listener code need a watcher restart, like changes to the timer. Everything else stays hot-reloaded.

**Ingest in the source-selected sweep.** Each sweep runs current code. Ingest gets its own budget: at most 16 MiB and 2 seconds per sweep, taken from the 5-second collection deadline. It runs even when no threads are selected. It ignores `.tmp` files.

1. Read spool files in name order. Decode OTLP/JSON: `resourceLogs → scopeLogs → logRecords`.
2. For **every** record of any event type, note `(session.id, process, event.sequence)`, so that sequence gaps can be detected. A process is identified by its first sequence-0 timestamp for that session.
3. For `api_request` records, keep only the allow-listed attributes above plus `app.version`. Drop `user.*`, `organization.id`, `terminal.type`, `vcs.*` and custom resource attributes. Store the row in `claude_request_events`.
4. Count `api_error` records per session in `claude_request_errors`. They have no token fields and are not billed as requests, so they never enter `claude_request_events`.
5. **Identity.** Key a request by `request_id`. An event without `request_id` is stored with its `client_request_id`, marked `unjoinable`, and never priced as a side request, because it may duplicate a transcript request. An identical duplicate is ignored. A conflicting duplicate is a gap.
6. **Malformed records.** A record missing a required attribute is rejected on its own, with a gap row. The rest of the file is still ingested. A file that cannot be parsed at all is rewritten into `otlp-spool/rejected/` with only the allow-listed attributes kept, and a gap row is written.
7. Delete each spool file only after its rows are committed.

**Retention.** The receiver sees every Claude Code session on the machine, including sessions not linked to beads. A session can be linked after it starts; this session filed its bead after starting. So Hive keeps event rows for unlinked sessions for **7 days**. This is a second explicit invariant change (§9, step 7). Other retention rules:

- Raw spool files and `rejected/` files older than 7 days are deleted, with a visible gap.
- Retention never runs in a sweep whose Beads link refresh failed (`registry_error`). Otherwise every session would look unlinked, and all of their events would be deleted.

**Joining the sources.** For each request identity, the merged row has `source` = `both`, `transcript` or `events`:

- **`both`:** transcript fields win: cache TTL split, geo, web searches, and real agent and skill names. The event's token counts must agree with the transcript's. `input_tokens` must equal the uncached count, and `cache_creation_tokens` the total cache write. Acceptance confirms these meanings. A disagreement is a gap, and the transcript values are used.
- **`events` only:** a side request, or a request from a file Hive could not read. It is priced from the event:
  - Speed `"normal"` maps to `standard`.
  - Service tier is assumed `standard` (flag `tier_assumed`).
  - Unknown cache TTL and geo are **derived, not guessed**. For a known model, `cost_usd_micros` is linear in the 5-minute/1-hour write split, and geo multiplies it by 1.0 or 1.1. Hive tries geo 1.0, then 1.1. It picks the first case whose solved split is an integer in `[0, cache_creation_tokens]` within micro-dollar rounding. It flags the row `cache_ttl_derived` or `geo_derived`.
  - If neither case solves, the writes are priced at the 1-hour rate, because Claude Code mostly writes 1-hour entries (§4.3). The row is flagged `cache_ttl_assumed`.
  - Web searches are unknown (flag `web_search_unknown`).
  - The quote is stored in `response_estimates` like any other quote, on the same best-effort basis, so once stored it does not reprice. The row also keeps `host_cost_usd` from `cost_usd_micros`.
- **`transcript` only:** normal while events are disabled. While events are enabled, it shows the listener missed that request.

**Coverage and completeness.** For each thread, the report adds:

- `event_coverage`: transcript requests with a matching event, divided by **all** transcript requests in the thread.
- `event_sequence_gaps`: missing `event.sequence` numbers within the thread's processes. These catch lost batches that held only side requests, which transcript coverage cannot see. The count is exact only when every event reaches the listener. That is why step 2 of ingest records every event type.
- `event_only_requests` and `event_only_usd`, grouped by `query_source`.
- `unjoinable_events`, `listener_rejections` (503s during the thread's lifetime) and `rejected_records`.
- `join_versions`: the Claude Code versions seen on `both` rows. A version where no event joins to a transcript request is reported as a join failure.

`complete_estimate_usd` is filled only when **all** of the following hold. Otherwise it is `null`, with the failing conditions listed:

- `event_coverage` is 1.0;
- `event_sequence_gaps`, `unjoinable_events`, `listener_rejections` and `rejected_records` are all 0;
- when a comparable `cost-state` exists (§5.6), the summed `cost_usd_micros` matches it within one micro-dollar per request.

When the estimate is complete, it adds transcript and event-only amounts and lists any flags that were derived or assumed.

### 5.8 Request detail model for a future spend UI

A later UI should show token spend for a bead, split by time, model, subagent, skill, subsystem and tool. Phase 1 stores what that UI needs, so no second pass over transcripts is required later.

One **merged request row** per API request, for both hosts:

```text
request_detail(
  host, thread, response,               -- identity; response = req_… / resp_…
  source,                               -- both | transcript | events
  observed_at, prompt_id, turn,         -- timing and grouping; observed_at is defined per source in §5.9
  agent, agent_type, parent_agent,      -- subagent (Claude); NULL for Codex
  query_source, skill, plugin, mcp_tool,
  model, modifier_key, effort,
  uncached_input, cache_read, cache_write_5m, cache_write_1h, output, thinking_output, web_searches,
  usd_input, usd_cache_read, usd_cache_write_5m, usd_cache_write_1h, usd_output, usd_server_tools,
  host_cost_usd,                        -- Claude Code's own figure when an event exists
  flags                                 -- possibly_partial, cache_ttl_assumed, geo_assumed, …
)
```

- This is a SQLite **view** over `responses`, `claude_request_events`, `claude_agents` and `response_estimates`. It is not a copy. Amounts come from the stored quote, so they do not reprice once stored. A request whose quote has not yet been stored is priced from the current rate card and flagged `unretained`. Splitting money by token category is exact integer arithmetic on the quote's rates.
- Codex responses can have stored quotes for several tiers. The view takes a `tier` parameter (via `hive cost --tier`), so each Codex response appears once. Claude rows use their own `modifier_key`.
- Step 5 creates the view with transcript-derived columns. Step 7 adds `source`, `query_source`, `plugin`, `mcp_tool`, `effort` and `host_cost_usd`. Step 8 adds `parent_agent`. Until then those columns are NULL.
- Phase 3 adds `tool_allocation(response, bucket, tool_use_id, tool_name, component, tokens, usd, method)`, and its rows sum to that request's amount.
- `hive cost --task ID --requests --json` returns these rows, 500 per page with a cursor, for a UI or for ad-hoc analysis. Codex rows have NULL Claude-only fields.
- Bead views are defined in §5.9. They use `observed_at` and `thread`.

### 5.9 Per-bead breakdown

**The problem.** Costs are observed per thread, but one thread often works on several beads in turn, and several threads can work on one bead. `hv-4up` is a real example: Codex thread `01a0d0bb` claimed it, and Claude session `1adb320e` took it over and closed it.

**Today's link reader misses earlier owners.** `thread_links.decode()` reads only the *current* assignee. `CollectionRegistry.refresh` then deletes any `collection_tasks` row that is no longer linked. So the Codex thread that did most of `hv-4up`'s work is not collected at all. This is an existing bug, fixed in its own commit (§9, step 9).

**Source of ownership: the Beads `events` table, not `bd history`.** Two sources exist. A third cold review showed that `bd history` is unsuitable:

- `bd history` returns Dolt commit versions. With `--dolt-auto-commit off`, the live store committed closes 17–32 minutes after they happened, and batched several beads' changes into one commit. A handoff inside one uncommitted window disappears, and history lags what `bd list` shows.
- Every commit adds a version to every bead (`hv-4up`: 61 versions, 83 KB after about 4.5 hours), so reads grow with the whole store.
- The `events` table is written in the same transaction as each change, is indexed by `created_at`, and records the actor and the changed fields:

```text
hv-4up  created         actor 01a0d0bb  2026-09-23T17:07:43Z
hv-4up  claimed         actor 01a0d0bb  2026-09-23T17:07:49Z  {"assignee":"01a0d0bb-…","status":"in_progress"}
hv-4up  updated         actor 1adb320e  2026-09-23T20:12:35Z  {"assignee":"1adb320e-…"}
hv-4up  closed          actor 1adb320e  2026-09-23T20:16:14Z  (close reason text)
hv-b4r  status_changed  actor 3583abd8  2026-09-23T21:15:57Z  {"status":"deferred"}
```

The local store holds these event types: `created`, `claimed`, `updated`, `status_changed` and `closed`. `reopened` is expected but has not been seen yet.

**Event times: use the UUIDv7 event ID, not `created_at`.** The `created_at` column is a naive `datetime DEFAULT CURRENT_TIMESTAMP`. It holds server-local time labelled as UTC: every `created` event in the local store is exactly 7 hours behind its bead's UTC `created_at`, and `@@system_time_zone` reports only the abbreviation `PDT`. Every event `id`, however, is a UUIDv7. Its first 48 bits are Unix milliseconds in UTC. For all 59 local events, that time agreed with `created_at` + 7 h to within 1 second, which is `created_at`'s truncation. So:

- An event's time is its UUIDv7 timestamp, which gives millisecond precision in UTC with no time-zone handling.
- **Cross-check:** `created_at`, converted with the server offset read at query time (`TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())`) or that offset ±1 hour to allow for daylight saving, must agree with the ID time within 2 seconds.
- An event whose ID is not a UUIDv7, or that fails the cross-check, makes its bead `interval_unknown` (below).

**Reading events cheaply.** `BeadsProcess` gains two fixed read-only queries. Both go through the same explicit routing as `list_all`, as `bd … sql --json`. Hive never sends any other SQL.

- `bd sql` cannot run under the global `--readonly` flag, which rejects it. It also has no parameter binding. So Hive builds each query from a constant template and inserts only a strictly validated value: a lowercase UUIDv7 string matching `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`, or a bead ID matching the existing `thread_links` pattern. The templates contain no write statements. A test asserts the exact query text.
- **Incremental query:** keyset paging on the UUIDv7 primary key, whose binary collation sorts by time. Status and assignee are extracted in SQL, so neither description text nor full `old_value` rows are transferred.

```sql
SELECT id, issue_id, event_type,
  CASE WHEN JSON_VALID(old_value) THEN JSON_UNQUOTE(JSON_EXTRACT(old_value,'$.status')) END   AS old_status,
  CASE WHEN JSON_VALID(old_value) THEN JSON_UNQUOTE(JSON_EXTRACT(old_value,'$.assignee')) END AS old_assignee,
  CASE WHEN event_type <> 'closed' AND JSON_VALID(new_value)
       THEN JSON_UNQUOTE(JSON_EXTRACT(new_value,'$.status')) END                              AS new_status,
  CASE WHEN event_type <> 'closed' AND JSON_VALID(new_value)
        AND JSON_CONTAINS_PATH(new_value,'one','$.assignee')
       THEN CASE WHEN JSON_TYPE(JSON_EXTRACT(new_value,'$.assignee')) = 'NULL' THEN ''
                 ELSE JSON_UNQUOTE(JSON_EXTRACT(new_value,'$.assignee')) END END             AS new_assignee,
  created_at, TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS server_offset
FROM events WHERE id > '<validated uuid>' ORDER BY id LIMIT 500
```

This exact query ran against the live store. Extraction needs the `JSON_VALID` guards, because `created` and `closed` events store an empty `old_value`, and `closed` stores its reason text as `new_value`.

- **Paging:** while a page comes back full, the next page starts strictly after its last `id`. Once a page is short, the cursor is caught up. The next sweep then starts from the last ID whose UUIDv7 time is **10 minutes** before the newest ID seen. That catches events that became visible late. Events are deduplicated by `id`. Nothing depends on the naive `created_at`, so a daylight-saving fall-back cannot hide events.
- **Per-bead query**, used for rebuilds: the same columns with `WHERE issue_id = '<validated id>' ORDER BY id`.
- **Budget:** a sweep reads pages until its Beads budget is spent. That budget is 2 seconds, shared with link refresh and counted inside the 5-second collection deadline, next to event ingest's 2 seconds (§5.7). A live test showed about 0.13 seconds per call. Collector status shows `bead_events_behind` when the cursor has not caught up.
- **Version and schema:** the `sql` command and the `events` schema are Beads internals. The bd 1.2.2 pin lives in `scripts/prepare_server_tools.py`. At runtime, Hive checks the columns returned by the first page, which needs no extra call. An unexpected shape disables bead attribution with a visible error and leaves thread costs unaffected.

**Replay rules.** Hive replays each bead's events in UUIDv7 order (millisecond time, then the sequence bits) to track `(status, assignee)`. The assignee is normalized to `""`, meaning none, when it is SQL NULL or a missing key (for example `old_assignee` on an unassigned bead) or a JSON `null` (mapped in SQL, because `JSON_UNQUOTE` returns the string `"null"` on this Dolt server). The same applies to `old_status`. A test covers an explicit `"assignee": null`.

| Event | Effect on replayed state |
|---|---|
| `created` | `(open, none)`. Checked later against the first before-state (below). |
| `claimed` | status and assignee from `new_value`. |
| `updated`, `status_changed` | Only the `status` and `assignee` keys present in `new_value` change. Other updates change nothing. |
| `closed` | status becomes `closed`. `new_value` is the close reason and is ignored. |
| `reopened` | Not yet seen locally. Its `new_value` keys are applied if present, else status becomes `open`. Acceptance records its real shape. |

**Per-event check.** For each event with a JSON `old_value` (`claimed`, `updated`, `status_changed`), the extracted before-state must equal the replayed state. That catches a missing or out-of-order event at the exact point it happened. For example, a missed A→B handoff followed by B→C shows up at B→C, because its before-state is B but the replay says A. The end-of-replay comparison alone would miss that case.

- A `created` event followed by a before-state with an assignee means the bead was created already assigned. The replay adopts that assignee from creation, without an interval unless the status is `in_progress`. This is not an error.
- On a mismatch, Hive rebuilds that bead from the per-bead query on the next sweep. A mismatch that is still there after two sweeps with no new events for that bead makes the bead `interval_unknown`. It stays unknown until a later rebuild passes; the state is recomputed each sweep, not permanent.

**Ownership intervals.** An interval `(bead, thread, start, end)` is open while the replayed state is `in_progress` with assignee `T`. It starts and ends at the UUIDv7 times of the events that enter and leave that state. An open interval ends at "now" for reporting.

**End-of-replay check.** The final replayed `(status, assignee)` must equal the current `bd list` row. The two reads are separate calls, so a change between them can cause a mismatch. A mismatch is therefore handled like a per-event mismatch: rebuild, and mark the bead unknown only if it persists across two sweeps with no new events.

**Deleted and renamed beads.** The events table cascades on delete and on update.

- A bead missing from `bd list` whose events are gone keeps its cached intervals, marked `deleted`. Its spend stays attributed to its ID, flagged. `bd delete`, `prune` and `purge` therefore do not silently move spend.
- **Renames are found from the `bd list` side.** `bd rename` and `bd rename-prefix` rewrite `issue_id` on existing event rows, because the foreign key also cascades on update. Those rows keep their IDs, which are already behind the cursor, so paging never sees the change. On each sweep:
  - A listed bead with no cached replay gets a per-bead rebuild.
  - A cached bead missing from the list is queried again by its old ID. An empty result marks it `renamed-or-deleted`, and its cache is dropped only once the rebuilt new ID accounts for the same intervals.
  - `bd rename-prefix` is listed in the operations warning. Afterwards the operator resets the events cursor.

**Other causes of `interval_unknown`:** a legacy bead with no events, an unexpected schema, or an event whose ID is not a UUIDv7 or fails the time cross-check.

**Links and collection.**

- Links become the union of current creator and assignee links, every interval owner in the cache, and every assignee seen in the events of an `interval_unknown` bead.
- The registry never drops a thread that still appears in any of those.
- Collector status reports `bead_events_caught_up` (the cursor reached a short page) and, separately, `interval_unknown_beads`.
- §5.7 retention is skipped only while `bead_events_caught_up` is false. An unknown bead does not block retention, because its assignees stay linked.

**Assigning requests.** Every priced request has a thread and an `observed_at` time. The time comes from a different source for each host:

- Claude transcript: the first content-block line's `timestamp` for that request.
- Claude event-only: `event.timestamp`.
- Codex: the `token_usage_record` timestamp, written when the response completes.

Subagent and side requests use their own times and their parent thread. Then:

- **Owned:** the time falls in exactly one interval of that thread, compared as `start ≤ t < end`. The whole amount goes to that bead.
- **Shared:** the time falls in two or more intervals of that thread, because one thread held several beads at once. The amount is split equally in integer picodollars, with a largest-remainder split in bead-ID order, and the request is flagged `shared`. This is the one allocation rule, and the invariant says so explicitly (below).
- **Unowned:** the time falls in no interval, for example scoping before a claim, work after a close, or filing. The amount stays on the thread as `unowned_usd` and is never guessed onto a bead.
- **Unattributable:** the thread's time overlaps a bead flagged `interval_unknown`.

Event times have millisecond precision. Request times are when a line or record was written, not when the request started, so requests within 2 seconds of an interval boundary are counted as `near_boundary`.

**Process guidance.** Attribution is only as good as claiming. The skills should say to claim before substantive work. Follow-up work after a close goes in a **new bead** rather than a reopen, because reopening means clearing the assignee after coordinating (`docs/operations.md`) and disturbs dependents. This design's own revisions after `hv-67e` closed are unowned for exactly this reason, until `hv-0m1` was claimed.

**Output.** `hive cost --bead ID [--tier T] --json`. `--tier` applies to Codex rows only, as in §5.8.

```text
{
  "code": "BeadCost", "bead": "hv-4up",
  "attributed_usd": "…",              // owned plus shared shares
  "intervals": [
    {"thread": "01a0d0bb-…", "host": "codex",  "start": "2026-09-24T00:07:49Z", "end": "2026-09-24T03:12:35Z", "usd": "…"},
    {"thread": "1adb320e-…", "host": "claude", "start": "2026-09-24T03:12:35Z", "end": "2026-09-24T03:16:14Z", "usd": "…"}
  ],
  "by_host": […], "by_model": […], "by_hour": […],
  "by_agent": […], "by_skill": […],   // Claude rows; null before step 8
  "by_query_source": […],             // null before step 7
  "by_tool": […],                     // null before step 11
  "shared_requests": 0, "near_boundary_requests": 0,
  "interval_status": "known",         // or "unknown", with the reason
  "coverage": {                       // conservative and thread-scoped
    "threads_with_unpriced": [], "threads_with_source_gaps": [],
    "threads_without_complete_estimate": ["01a0d0bb-…"]
  },
  "not_captured": "Threads that never held the bead (for example Codex reviewer threads that did not claim it) are not included.",
  "creator_threads": ["01a0d0bb-…"]   // listed for context, no amounts
}
```

- Coverage flags cover each thread's whole life, not just its interval, because source gaps are recorded as file positions and cannot be mapped to times. So the flags are conservative.
- Each breakdown sums to `attributed_usd` exactly.
- **Consistency check** (`hive cost --reconcile [--tier T]`), which confirms bookkeeping, not accuracy. It covers requests with a stored quote, transcript and event-only, for the given Codex tier. For those requests, the sum over beads of `attributed_usd`, plus each thread's `unowned_usd` and `unattributable_usd`, equals the sum of the thread totals. Unpriced requests are counted separately. Quotes are stored on a best-effort basis after each report, so `--reconcile` first stores quotes for any unquoted priced request. If contention prevents that, it reports `unretained_estimates` instead of a result.
- Parent and child beads (epics) are not rolled up automatically. Each bead's amount is exact and non-overlapping, so a UI can add up the children it chooses.
- **Codex breakdowns are thinner.** Codex rows have no agent, skill or query-source dimensions, so those breakdowns show them as `null`.
- `cost_report.py`'s comment "No task ownership is read here" stays true for thread reports. The bead report lives in a new module.

**Invariant changes** (`docs/invariants.md`):

- In step 9: "Observation derives thread IDs only from bead creator metadata and native assignee fields" becomes: "…from bead creator metadata, native assignee fields, and bead ownership-interval owners recorded in the Beads events table."
- In step 10: "Costs are per thread, with associations" becomes: "Costs are observed per thread. A request is charged to a bead only when its thread held that bead's ownership interval, derived from Beads events, at the request time. When a thread held several beads at once, the request is split equally among them. Creator links and unowned spend are never allocated."

## 6. Phase 2: subagent and skill breakdown

Both breakdowns are exact partitions of the priced total. They need no estimation.

**By agent.** Each response already carries `agent`. The report adds:

```json
"by_agent": [
  {"agent": null, "label": "main", "responses": 180, "usd": "1.484430000000"},
  {"agent": "a552bcdfb757ccd4b", "agent_type": "general-purpose",
   "description": "Cold warden review of hv-4up", "spawned_by_tool_use": "toolu_013uw…",
   "parent_agent": null, "spawn_depth": 1, "responses": 34, "usd": "0.480670000000"}
]
```

- A nested subagent's `parent_agent` is the agent whose transcript contains `spawned_by_tool_use`. That link is found by indexing `tool_use` IDs per file.
- If a meta file is missing or unreadable, the agent still appears, with `parent_agent: "unknown"`. Its cost still counts.
- `spawn_depth` is nullable: older versions omit `spawnDepth`, and newer ones add fields such as `requestShape` that Hive ignores.
- Older transcripts that put sidechain records inline in the main file (`isSidechain: true` there, with `agentId`) are grouped the same way.

**By skill.** Each `assistant` line carries an `attributionSkill` field, for example `"executor"`. Grouping by that field is cheap and exact. The field is undocumented, so a missing value is reported as `null` rather than inferred.

**Codex.** Codex does not record subagents in the same thread. The observed `root_turn_id` hints at a parent turn. Spawned Codex threads have their own thread IDs and are covered only when a bead links them. No Codex change is planned here.

## 7. Phase 3: tool attribution (estimated allocation)

### 7.1 What "cost of a tool call" means

In an agent loop, a tool call costs little to emit. Its real cost is the result it adds to the prompt, which is re-read, mostly from cache, on every later request until the context resets. So a meaningful attribution needs two parts:

- **Invocation cost:** the output tokens spent writing the `tool_use` block.
- **Carrying cost:** the prompt cost of the tool's input and result, summed over every later request that includes them.

Two simpler alternatives were rejected:

- **"Cost of the request that called the tool."** This is exact and easy, but it charges the whole re-read context to whatever tool the model chose next. That tells you nothing about which tools are expensive.
- **Token counting through the API** (`count_tokens`) for each result. This is accurate, but it sends transcript contents to an external service, which counts as publishing. It also costs money and needs credentials. Hive's observer must stay local and read-only.

### 7.2 Positional pricing, which is exact given segment sizes

Caching is a prefix match. Every request's prompt is ordered, `tools → system → messages`, and its token positions are priced by band:

```text
positions [0, cache_read)                      → read rate
positions [cache_read, cache_read + cw)        → write rate (5m band first, then 1h — see below)
positions [cache_read + cw, P)                 → uncached input rate
```

Split the prompt into **segments** in order. Segment 0 is the base (tools, system and the first user message). Segment *k* is everything added between request *k−1* and request *k*. Its size is measured exactly:

```text
size(k) = P(k) − P(k−1)          where P = input + cache_creation + cache_read
```

Request *n*'s prompt cost then splits across segments 0..n exactly, by where each segment's tokens fall in the bands. The sum over all segments equals the request's priced input cost, with no rounding: each segment gets token count × band rate.

The order of the 5-minute and 1-hour write bands inside the write range is not reported. The design puts the 1-hour band first, because Claude Code marks earlier prefixes with 1-hour TTL, and records this as an assumption. Only the split between segments inside the write range depends on it. The request total does not.

### 7.3 Splitting a segment between its parts (the estimated step)

One segment can hold several parts: the previous assistant turn (text, thinking, `tool_use` blocks), one or more `tool_result` blocks from parallel calls, system reminders and attachments, and user text. The transcript gives no per-part token counts. Hive splits `size(k)` in proportion to each part's **serialized byte length**, using what the model actually saw:

- For `tool_result`, use `message.content`, **not** `toolUseResult`. `toolUseResult` can hold the full output even when the context received a truncated preview.
- For `tool_use`, use the serialized `input` JSON.
- **Thinking is never weighted by bytes.** In the sample, 144 of 523 thinking blocks had empty text and only a signature, so zero bytes stood for many tokens. Thinking kept in the prompt goes to its own `thinking` bucket. Its size is estimated as the previous response's `thinking_tokens`, capped at the segment size, and taken out of the segment before the byte split. This is labelled estimated: whether a model keeps earlier thinking in the prompt, and how it re-tokenizes it, varies by model. When `thinking_tokens` is absent, thinking gets no deduction and the report counts `thinking_unmeasured`.
- For user text, use the rendered text.
- **Attachments and reminders:** only records that Claude Code sends to the model count. These are attachments with a `rendered` or content field that reaches the prompt, plus `<system-reminder>` text inside user messages. Records that never reach the model (`file-history-*`, `custom-title`, `agent-name`, `queue-operation`, `cost-state`, `mode` and similar) have zero weight. An attachment kind not on the known list is weighted by bytes and counted in `unknown_attachment_kinds`.

**Rounding.** Each band of a segment has an integer token count. Splitting it by bytes gives fractions. Hive uses a largest-remainder split per (segment, band): floor each share, then give the leftover tokens to the parts with the largest remainders, breaking ties by part order. Money is computed only on integer tokens, so the sum stays exact.

Byte-proportional splitting is an estimate: tokens per byte differ between code, JSON and prose. Each part's allocation is labelled `method: "bytes"`. A segment with a single part is `method: "exact"`.

**Invocation cost** (output side). The output cost of response *k* is split across its content blocks. When `thinking_tokens` is present, thinking gets exactly that many output tokens. The rest is split between text and `tool_use` blocks by byte length.

### 7.4 Buckets and reconciliation

Every priced token lands in one bucket:

- `tool:<name>`, split into `invocation` and `carrying`. `<name>` is `Bash`, `Read`, `Agent`, `mcp__…` and so on. `Agent` rows also show the delegated subagent total from Phase 2, as a separate exact figure that is **not** added in a second time.
- `base_context`: tools, system prompt and first user message (segment 0).
- `user_input`, `assistant_text`, `thinking`, `reminders_and_attachments`.
- `rewritten_context`: see §7.5.
- `server_tool_fees`: web search.

The report checks that the bucket sum equals `priced_subset_usd` exactly, in integer picodollars. If it does not, the report shows a nonzero `unallocated_usd` and `allocation_error` rather than adjusting the numbers.

This check is an **internal consistency test only**. Every token is assigned to some bucket by construction, so a pass shows no implementation bug, not that the allocation is accurate.

**Accuracy measurement.** Segments with a single part give exact token counts for that part's kind and byte length. From them Hive fits a tokens-per-byte ratio for each part kind, then re-estimates those same segments by bytes and reports the median and 90th-percentile error as `byte_split_error`. The ratios are fitted per report and are not stored as constants. With fewer than 20 single-part segments, `byte_split_error` is `null`.

```json
"by_tool": [
  {"tool": "Bash", "calls": 41, "invocation_usd": "0.0213…", "carrying_usd": "0.4127…",
   "exact_segments": 30, "byte_split_segments": 11},
  {"tool": "Agent", "calls": 1, "invocation_usd": "…", "carrying_usd": "…",
   "delegated_subagent_usd": "0.480670000000"}
],
"allocation": "estimated: segment sizes measured from usage; multi-part segments split by bytes; 1h-before-5m write band order assumed"
```

### 7.5 Where the append-only model fails

The chain is broken when `P(k) < P(k−1)`. That happens on compaction, context clearing or a harness rewrite. It also counts as broken when a `compact_boundary` or `microcompact_boundary` system record appears between the two requests. The step is then marked as a **reset**:

- Request *k*'s prompt cost goes to `rewritten_context`.
- Allocation starts again from request *k*. The next segment is measured against `P(k)`.
- The report counts `context_resets`.

A cache expiry does not break the chain. Positions stay the same; only the bands move.

**Rewrites that don't shrink the prompt.** A change in the middle of the prompt, such as a new tool list, a changed reminder or a partial microcompact, can leave the prompt growing. The new tokens would then be charged to the latest parts. Hive flags a step as `suspected_prefix_rewrite` when both hold:

- `cache_read(k) < P(k−1)`: the new request did not reuse the whole previous prompt;
- the time since request *k−1* started is shorter than the shortest TTL written in the prompt (5 minutes if any 5-minute write is present, otherwise 1 hour), so expiry cannot explain the miss.

A flagged step is treated like a reset: request *k*'s prompt cost goes to `rewritten_context`, and allocation restarts from *k*. The report counts these steps separately from `context_resets`.

### 7.6 What Phase 3 stores

Transcripts are not copied. Each record adds small rows to the store:

- `segment_parts(task, agent, response, ordinal, kind, ref, name, bytes)`: parts seen between the previous response and this one, in file order. `ref` is the `tool_use_id`. `kind` is one of `tool_use | tool_result | text | thinking | reminder | attachment | user`.
- `response_blocks(response, ordinal, kind, name, ref, bytes)`: the output-side blocks.

"Between two responses" means in file order within one agent's transcript. Records in one file are appended by one agent loop, so this matches prompt order. Out-of-order or queued records are an accepted limitation, disclosed through `byte_split_segments`.

**Oversized lines.** A tool result can exceed `MAX_LINE` (256 KiB). The chunk reader skips the line and records a gap. Phase 3 then records an `oversized` part whose byte length is the number of skipped bytes. `transcript_chunks.read` needs a small change to report that length. The segment's split is labelled `method: "bytes_with_oversized"`.

## 8. Failure modes and how they surface

| Situation | Behaviour |
|---|---|
| Transcript format changes (new usage keys, new iteration types) | Unknown keys are ignored; missing required keys and new iteration types become gaps or unpriced reasons. Nothing is guessed. |
| Session ID found under two project directories | Discovery gap; the thread is not collected. |
| Subagent file appears later | Picked up on the next pass with its own cursor. |
| Stream cut off before the final line | Priced with partial output; counted in `possibly_partial_output`. |
| New model released | `unknown_model_price` until the rate card is updated. The stored quote keeps old evidence. |
| Fast mode or US inference | Priced from observed modifiers. Other modifiers are unpriced with a reason. |
| Unrecorded side requests | Without events: lower-bound disclosure plus `host_reported` and `unrecorded_usd_lower_bound`. With events: `event_only` detail rows. |
| Listener down, spool full, or settings not applied | `event_coverage` below 1.0, sequence gaps or `listener_rejections`, and `complete_estimate_usd = null`. |
| Port already in use | The timer keeps running; the bind failure appears in collector status. |
| Event format changes between Claude Code versions | Unknown attributes are ignored. A record missing a required attribute is rejected alone, with a gap. `join_versions` shows a version whose join broke. |
| Fake events posted to the port | Requires the bearer secret. The event and transcript token counts must agree for `both` rows. |
| Beads links unavailable during a sweep | Retention is skipped, so no event rows are deleted. Cached bead intervals are kept. |
| Bead events missing, schema changed, or a replay check fails across two sweeps | That bead is `interval_unknown`; overlapping spend is `unattributable`, never charged to the current assignee. Its assignees stay linked. |
| Bead deleted or renamed | Deleted: cached intervals kept, marked `deleted`. Renamed: found through `bd list`, and the new ID is rebuilt from the per-bead query. |
| Event ID not UUIDv7, or its time disagrees with `created_at` | That bead is `interval_unknown`. |
| Events backlog larger than one page | Keyset paging works it off over later sweeps; `bead_events_behind` in status; retention skipped until caught up. |
| Events pulled from another machine, or restored from backup | Pulled events can have IDs below the cursor. Operations tells the operator to reset the events cursor after a pull or restore. A different time zone fails the cross-check and is reported. |
| Thread keeps working after closing a bead | That spend is `unowned_usd` on the thread, not charged to the bead. |
| Reviewer thread never claims the bead | Not included; `BeadCost.not_captured` says so. |
| Compaction or context rewrite | `rewritten_context` bucket and `context_resets` count. |
| Transcript rewritten in place | Existing device/inode/size check makes a gap and restarts that file's cursor. |
| Collector runs older code against a migrated store | Before step 1: the call fails and rolls back, with no data loss (§5.4). After step 1: the `user_version` guard raises a clear error. |

## 9. Delivery plan

Each step is one reviewable Conventional Commit, delivered through Tollgate with a fresh cold review, per `AGENTS.md`.

1. `refactor(telemetry): add host-neutral thread identity and schema version`. Introduces `Host`, `ThreadId`, `Owner.agent` and `user_version`. Migrates rows to `host='codex'`. No behaviour change. Black-box: the existing `tests/test_usage.py` and `tests/test_cost.py` pass unchanged against a migrated older database.
2. `feat(telemetry): discover and collect Claude Code transcripts`. Adds bootstrap `claude_projects`, the two-host probe, per-file cursors, subagent listing and `claude_usage.decode` with the partial-output upsert. Tests use fixture transcripts built from the shapes in §4, including a copy duplicated across files, a partial stream, a `<synthetic>` line, a mismatched `sessionId` and a missing cache TTL split.
3. `feat(cost): price Claude responses from published rates`. Adds the rate table, modifiers, web search fees, quote evidence keys, the report fields and the `--tier` rejection. Tests: one case per model/modifier combination with hand-computed picodollar totals, and stored quotes surviving a rate-card edit.
4. `feat(cost): report Claude host totals and unrecorded lower bound`. Reads `cost-state` and adds the single-process comparison rule.
5. `feat(telemetry): expose per-request cost detail`. Adds the `request_detail` view and `hive cost --requests` with paging (§5.8).
6. `feat(telemetry): receive Claude Code request events on localhost`. Adds the `--otlp-port` listener, secret, limits, spool, rejections log, `otlp-config` command and collector status fields. It amends `docs/invariants.md` so the resident observer may also keep a loopback log listener that spools bodies without parsing them. `docs/operations.md` gains the port, secret, spool location, reset (stop the watcher, delete the spool and secret) and uninstall steps (remove the settings entries and the flag). Black-box tests post OTLP/JSON over loopback and check: the spool; the 401, 404, 405, 413, 415 and 503 cases; a non-loopback `Host`; a slow client; a bind failure; and that the watcher survives a source update.
7. `feat(cost): join request events with transcripts`. Adds spool ingest with its byte and time budget, attribute allow-listing, sequence tracking, 7-day retention with the registry-failure guard, the join rules, event-only pricing with derived TTL and geo, the coverage and completeness fields, and the view's event columns (§5.7). It amends `docs/invariants.md` to allow keeping unlinked sessions' request events for 7 days. Fixtures use real OTLP/JSON shapes captured during acceptance, with personal attributes replaced.
8. `feat(cost): break down thread cost by subagent and skill`.
9. `fix(telemetry): keep collecting past bead owners`. Reads the Beads events table (fixed validated queries, keyset paging, schema check, UUIDv7 event times). Links every thread that has ever owned a bead. Never drops a thread that is still linked by any of the rules in §5.9. Adds `bead_events_caught_up` and `interval_unknown_beads` to status. This fixes today's bug for Codex too. Amends the invariant sentence on where thread IDs come from, in this same step.
10. `feat(cost): break down spend by bead`. Adds interval replay with the end-of-replay check, owned/shared/unowned/unattributable assignment, `hive cost --bead` and `--reconcile`, the retention guard, an operations warning that `bd delete`, `prune`, `purge`, `rename-prefix`, `gc`, `compact`, `flatten`, backup and restore, and Dolt pull or sync from another machine can remove or hide attribution evidence (with the cursor-reset command), the skill wording on claiming and follow-up beads, and the cost invariant change (§5.9). Black-box tests use a disposable Beads store with: a handoff between two threads; a reopen; overlapping claims; a deleted bead; a mismatched replay; a cold start with a backlog larger than one page; a non-UUIDv7 event ID; events written during a daylight-saving fall-back hour; a missed middle handoff caught by the before-state check; a bead created already assigned; a `bd list` change racing the events read; more than one full page of events; a renamed bead and a `rename-prefix`; an explicit null assignee; and retention while intervals are incomplete.
11. `feat(cost): allocate thread cost to tool calls`. Adds Phase 3 tables, the positional allocation, reconciliation and the reset handling. Property-style test: for random segment sizes and bands, the bucket sum equals the request total exactly.
12. `docs: record Claude cost evidence`. Updates `docs/implementation.md` and `docs/invariants.md` ("Costs are per thread" gains "with exact subagent breakdown and estimated tool allocation"). The invariant changes from steps 6, 7 and 10 are already made there.

Steps 1–5 give Codex parity plus the request detail rows. Steps 6–7 add the event source. Step 8 is the exact breakdown. Steps 9–10 are the per-bead breakdown. Step 11 is the estimated tool allocation. Steps 6–7, 8, 9–10 and 11 can each be approved separately. Steps 9–10 need only steps 1–5. Without step 7, `by_query_source` is null; without step 8, `by_agent` and `by_skill` are null; without step 11, `by_tool` is null.

This plan itself is committed as `docs/claude-cost-plan.md` in a separate documentation commit (bead `hv-0m1`) before any implementation step.

Each step updates `docs/implementation.md`, and `docs/operations.md` where it applies, in the same commit, as `AGENTS.md` requires. For example, step 2 replaces "source transcripts remain under Codex control" with wording that covers both hosts. Step 12 only records the final acceptance evidence.

**Performance budget.** The collector keeps its 1 MiB per-file read and its 5-second batch deadline. A Claude thread with N subagents is N+1 files. `--batch-size` still counts threads. Within a thread, each pass reads the file least recently attempted first, then others while the 5-second deadline allows. So every selected thread advances at least one file per pass, and a thread with many subagents cannot starve the others. Event ingest takes at most 2 of the 5 seconds (§5.7).

## 10. Validation and acceptance

Automated, in `scripts/check`:

- Fixture-based black-box tests through `hive telemetry collect` / `sweep` and `hive cost --json`, as the Codex tests do today.
- An exact reconciliation assertion for Phase 3 on every fixture.
- Largest-remainder split tests, including ties, and a fixture with a mid-prompt rewrite that must be flagged.

Manual acceptance, recorded in `docs/acceptance.md`. No production activation is needed:

1. **Arithmetic check.** Pick a completed local session whose `cost-state` token counts equal the transcript sums. `4dd72542` is one today. `hive cost` must equal `cost-state.totalCostUSD` to the cent. Expected: $0.8905368.
2. **Gap disclosure.** Pick a session with a gap. `1adb320e` is one today. The report shows the transcript estimate, `host_reported`, and a positive `unrecorded_usd_lower_bound`.
3. **Resume behaviour.** Resume a disposable session twice. Record how `cost-state` records appear. Confirm the report reports one process segment and never adds segments together.
4. **Subagents.** A session with a nested subagent (`spawnDepth: 2`) shows the correct `parent_agent`.
5. **Fast mode and US inference.** One disposable request each, if the account allows it. Otherwise record these as unverified.
6. **Tool allocation consistency.** On every accepted session, `unallocated_usd = 0` and every reset or suspected rewrite is counted.
7. **Collector continuity.** Run `hive telemetry watch` with a mixed Codex and Claude bead set across a source update, with a sweep in flight when the new commit is promoted. The watcher keeps running. At most one batch fails transiently with no data change, and the next batch migrates and collects both hosts.
8. **Codex re-read after migration.** Re-collect a pre-migration Codex transcript with `--from-start`. Expect zero new gaps, the same response count and the same total.
9. **Every subagent file.** For each accepted session, the number of collected subagent files equals the number of `agent-*.jsonl` files on disk. `f8e5bf9f` (two subagents) must give $2.19883.
10. **Event source.** Apply the §5.7 settings. Run the watcher with `--otlp-port`. Start a fresh desktop-app session that runs one subagent. Running this against a real session counts as observation only; it is not production activation, and it is recorded that way. Then check:
    - whether managed settings removed any of the variables;
    - `session.id` equals `CLAUDE_CODE_SESSION_ID`;
    - event `input_tokens` equals the transcript's uncached count on `both` rows;
    - `event_coverage` is 1.0 and `event_sequence_gaps` is 0;
    - `event_only_requests`, grouped by `query_source`, is recorded. It may be 0; `4dd72542` had no gap;
    - the summed `cost_usd_micros` matches the final `cost-state` within one micro-dollar per request;
    - no `user.*` attribute is stored;
    - `/clear` and resume behave as §5.7 describes.
    Then stop the watcher partway through a second session and restart it. The report shows sequence gaps or reduced coverage, and `complete_estimate_usd` is `null`.
11. **Detail rows.** `hive cost --requests` returns one row per request. Transcript rows sum to `priced_subset_usd`, and event-only rows sum to `event_only_usd`.
12. **Per-bead breakdown.** On the live store (read-only):
    - `hv-4up` shows two intervals: Codex `01a0d0bb` from 00:07:49.580Z to about 03:12:35Z, then Claude `1adb320e` until about 03:16:14Z. The Codex thread is collected.
    - `hv-67e` shows this thread's interval from 04:08:16Z to its close event near 04:16:03Z. This thread's requests from then until the `hv-0m1` claim at 04:33:43Z are `unowned_usd`. Its later requests go to `hv-0m1` until that bead closes.
    - Every event's UUIDv7 time passes the `created_at` cross-check. Every per-event before-state check passes. `hive cost --reconcile` balances exactly.
    - A sweep with a cold cache stays inside its deadline, and repeated sweeps reach `bead_events_caught_up`.
    The reopen, overlap, deletion, backlog and time-zone cases use a disposable store.
13. **Tool-split accuracy.** Report `byte_split_error` for at least three sessions. Phase 3 is accepted only if the user judges that error acceptable.

## 11. Extending Phase 3 to Codex (follow-up, not approved here)

Codex records `response_item` entries (`custom_tool_call` and `…_output` pairs with `call_id`), one `token_usage_record` per response, and prefix caching. The same segment engine applies once Codex decoding emits `segment_parts`. The differences are that Codex's `input_tokens` already includes cached tokens, and it has one cache-write class. Tracked as a separate bead after Phase 3 lands.

## 12. Cold review disposition

A fresh warden review (2026-09-23) reported 3 high, 7 medium and 8 low findings. All were accepted and are addressed above:

- **High:** quote identity per response (§5.5); stored usage JSON compatibility (§5.4); a table error in which an earlier draft dropped one of `f8e5bf9f`'s subagents (§4.6, with a new acceptance item in §10).
- **Medium:** Claude file identity check (§5.2); migration transaction and transition window (§5.4); thinking kept out of the byte split (§7.3); reconciliation is only a consistency check, so an accuracy measure was added (§7.4); rewrites that don't shrink the prompt (§7.5); `cost-state` comparison rule (§5.6); a single global response key (§5.4).
- **Low:** `--tier` default; per-pass scheduling; manual commands; nullable `spawn_depth`; discovery when the Codex index errors; docs updated in each step; attachment kinds; `cost-state` agreement is not an independent check.

The reviewer recommended approving Phases 1–2 and holding Phase 3 until its fixes were written. They are now written, but Phase 3's accuracy is still unmeasured (§10, item 13).

A second fresh review, of revision 2's event source and detail view, reported 1 high, 8 medium and 5 low findings. All were accepted:

- **High:** the completeness condition now needs full transcript coverage, no sequence gaps, no rejections, and agreement with `cost-state` (§5.7).
- **Medium:**
  - events without `request_id` and `api_error` events can no longer be counted twice;
  - malformed records are rejected one at a time;
  - `"normal"` speed is mapped, and TTL and geo are derived from `cost_usd_micros`;
  - both invariant changes are made explicit, with operations documentation;
  - retention is skipped when link refresh fails;
  - spool privacy and age limits are added;
  - ingest has its own budget;
  - the view's tier and step-by-step columns are defined, and the event tables are listed in §5.4;
  - the totals that each breakdown must match are named.
- **Low:** per-signal headers, `app.version`, metrics left untouched, listener hardening and bind failure, the secret kept out of transcripts, default port 4319, consistency fixes, and acceptance detail.

A third fresh review, of the per-bead section, reported 3 high, 7 medium and 4 low findings. All were accepted:

- **High:** `bd history` lags, batches and grows with the store, so ownership now comes from the transactional `events` table, read incrementally within a shared time budget. Links and retention are protected while the interval cache is incomplete.
- **Medium:**
  - a bead whose replay disagrees with its current row is `interval_unknown`, never charged to the current assignee;
  - interval ends come from events;
  - both invariant sentences change, including the equal-split rule;
  - acceptance now states exact windows and the cold-start, retention, budget and time-zone cases;
  - the link fix is its own step, with field availability stated per step;
  - reviewer threads that never claim are disclosed as not captured;
  - coverage is labelled thread-scoped, and creator amounts are dropped.
- **Low:** reconciliation totals are defined; `observed_at` is renamed and defined per source; follow-up beads are used instead of reopening; consistency nits fixed.
- **Found while verifying the fix:** `created_at` is server-local time labelled `Z`. Event IDs are UUIDv7, so Hive takes UTC times from the IDs and uses `created_at` only as a cross-check.

A fourth fresh review, of revision 5's events-based design, reported 2 high, 4 medium and 3 low findings. All were accepted:

- **High:** the cursor now uses keyset paging on UUIDv7 IDs with a 10-minute look-back, so a daylight-saving fall-back, late writes or a bulk import cannot drop events or stall paging.
- **Medium:**
  - each event's before-state from `old_value` is checked against the replay, and a mismatch is marked unknown only if it persists;
  - retention depends only on the cursor catching up;
  - query construction is specified: validated values, no `--readonly` (it rejects `sql`), JSON extraction in SQL, and the server offset as a column;
  - replay rules per event type are defined.
- **Low:** the thread-ID invariant change moves into step 9; deleted and renamed beads are defined; operations covers backup, restore and pull.

A fifth narrow review of revision 6 found two significant, non-blocking issues, both fixed: renames rewrite existing event rows, so they are detected through `bd list`; and JSON `null` assignees are mapped to none in SQL.

## 13. Decisions and open questions

Decided by the user on 2026-09-23:

- Rate tables stay hand-maintained in `pricing.py`, as for Codex.
- Capture per-request detail, not only Claude Code's totals, with a bead-level spend UI in mind. That decision added §5.7 and §5.8.
- Include the per-bead breakdown in this plan (§5.9).
- Commit this plan to the repository, as `docs/claude-cost-plan.md`.

Still open:

1. **Approval and scope:** approve which parts? Steps 1–5 (parity plus detail rows), 6–7 (event source), 8 (exact breakdown), 9 (per-bead breakdown), 10 (estimated tool allocation).
2. **Event source settings:** the operator must add the §5.7 settings to `~/.claude/settings.json` and run the watcher with `--otlp-port`. Is loopback port 4319 acceptable? Are 7 days acceptable for keeping unlinked sessions' events and raw spool files?
3. **Shared intervals:** when one thread holds two beads at once, is an equal split acceptable, or should those requests stay unallocated?
