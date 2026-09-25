# Plan: Hive web dashboard

- Epic: `hv-762` — **Deliver the Hive web dashboard**. Children: completed design `hv-amo`; implementation `hv-ijd` (steps 1–2), `hv-q9z` (step 3), `hv-l85` (steps 4–5), and `hv-286` (steps 6–8). Prerequisites and native dependency semantics are recorded in §12.
- Status: revision 5. The user approved the design on 2026-09-24; on 2026-09-25 the user rejected intermediate approval holds, corrected the weaver skill, and explicitly resumed implementation. This revision removes those holds while retaining technical scope and honest evidence reporting.
- Repository: `/Users/dthurn/hive`.
- **Technical foundation:** the complete cost implementation, including delivered `hv-k8r` and `hv-82b`, supplies pricing, request details and allocation. Remaining empirical and live-host evidence is disclosed in `docs/acceptance.md`; it is not an implementation approval gate.
- Related code: `src/hive_bootstrap/source.py` and `settings.py` (per-commit source selection, bootstrap config), `src/hive/launch_context.py`, `src/hive/collection*.py`, `src/hive/collection_registry.py`, `src/hive/usage_store.py`, `src/hive/cost_report.py`, `src/hive/thread_links.py`, `src/hive/beads_process.py`.
- Visual reference: Fulcrum's design-system mockups (`~/fulcrum/docs/mockups/design-system-*.png`, `newsfeed-desktop.png`). They set the visual style only. Content, names and emblems below are Hive's.

## 1. Summary

Hive gains a local web dashboard. It has one screen: a **newsfeed** of cards.

- A **bead card** for each bead, showing its attributed API-equivalent USD spend, the roles of the threads that owned it, its status and diagnostic badges.
- An **agent card** for each observed session that never owned a bead, and a **tail card** for a bead-owning session whose spend outside ownership is large.
- Two kinds of **ledger card** hold spend that belongs to no bead or session card: unattributable spend and small unowned tails. With them, the feed accounts for every priced dollar (§5.1).

Clicking a card opens a **detail view**:

- A timeline of spend across the card's life, with diagnostic markers: tool errors, slow tools, Tollgate CI failures, compactions, cache rewrites, API errors and human waits.
- A breakdown bar with a dimension switcher: role, thread, subagent, model, token category, skill, query source and tool.
- For beads, a **read-only Beads view**: every field, metadata, dependencies, comments and the native event history.

Above the feed, a summary strip shows spend by role for a time window. A **hotspot banner** names the largest inefficiency that fixed rules find.

The goal is one visual hub for finding where agent work wastes money and time.

## 2. Goals and non-goals

Goals:

- One feed covering every registered project, with project, role, state and text filters.
- Every dollar shown traces to request rows from the cost plan (§5.8 there). No new pricing logic.
- The feed's cards and ledger cards reconcile exactly with the cost plan's `hive cost --reconcile` identity.
- Diagnostics that point at a specific thread, subagent, tool call or candidate, with an on-demand excerpt.
- A usable read-only Beads UI: fields, flat metadata, dependencies, comments and events.
- Hot reload preserved: a promoted commit changes the dashboard on the next page load, with no restart.

Non-goals:

- **Writing to Beads.** The browser cannot create, claim, comment, edit or close beads. The detail view offers copyable `bd` commands with the routing prefix instead. Browser writes need an actor model and a CSRF design, which belong in a follow-up.
- Remote or phone access. The dashboard is loopback only (§8.4).
- LLM-generated insights. Hotspots are deterministic rules.
- Changing how agents work. Roles, CI links and diagnostics are inferred from existing records. The one workflow-facing change is optional: branch names that start with the bead ID improve CI matching (§4.4).
- Billing truth. Amounts inherit the cost plan's API-equivalent meaning and coverage flags.

## 3. Terms

- **Session** / **thread:** one Codex thread or Claude Code session, identified by its thread ID.
- **Subagent:**
  - A Claude subagent transcript inside its parent session.
  - A Codex **spawned thread**, which has `threads.thread_source = 'subagent'` and its parent in `source.subagent.thread_spawn.parent_thread_id`. There are 2,040 of the 5,896 local Codex threads.
  - Both are folded into their **root session** (§4.1).
  - Codex `agent_created_thread` threads are independent sessions, not subagents. They are handoffs that may claim beads themselves.
- **Ownership interval:** a `(bead, thread, start, end)` span from the Beads events table (cost plan §5.9).
- **Owned / unowned / unattributable spend:** these follow the cost plan's `attributed_usd`, `unowned_usd` and `unattributable_usd`.
- **Role:** the Hive skill behind a request: executor, bead, warden, weaver, sage, mason, vizier, justiciar or archivist. Otherwise the role is "ad hoc".
- **Candidate:** a Tollgate queue item, gate or push-master, with its attempts and step results.

## 4. What is observed

### 4.1 Sessions in Hive projects (invariant change)

Today observation collects only threads linked from bead creator metadata and assignees. After the cost plan, it also collects past interval owners. The dashboard also needs sessions that never touched a bead. The user chose to collect **every session whose working directory belongs to a registered project**, from a per-project start date.

**Configuration.** Each `projects[]` entry in `hive.json` gains an optional `observe_since` date (ISO format). Without it, project sessions of that project are not collected. Linked threads still are.

```json
{"id": "battlement", "repository": "/Users/dthurn/battlement",
 "invariants": "/Users/dthurn/battlement/AGENTS.md", "observe_since": "2026-09-24"}
```

`hive_bootstrap/settings.py` rejects unknown project keys today, and the application side (`LaunchContext`) has no access to `projects`. Step 1 extends `Project` with `observe_since` and passes the project list to the application. Operations must warn not to add the key before step 1 lands, because until then any key it doesn't know fails every `hive` call.

**Which directory belongs to a project.** Tollgate worktrees live under `<repository>/.worktrees/`. Codex worktrees live at `~/.codex/worktrees/<id>/<name>`; 10 local threads use them. So a path prefix is not enough. The rule is the same one `skills/shared/entry.md` uses:

1. Resolve `git -C <cwd> rev-parse --path-format=absolute --git-common-dir`. If it equals the project repository's common directory, the session belongs to the project.
2. Otherwise, if `cwd` resolves to a path under the repository with a separator boundary, it also belongs. That covers worktrees that were already removed. `/Users/dthurn/hive2` never matches `/Users/dthurn/hive`.
3. Otherwise the session doesn't belong. An unresolvable `cwd`, such as a removed Codex worktree, counts as `unresolved_cwd` in collector status.

The result is cached per `cwd` string in `cwd_projects`, so each directory is resolved once, when it is first seen.

**Discovery is incremental:**

- **Codex:** query the native index for threads with `updated_at_ms` past a stored cursor, re-reading the last 10 minutes. Keep threads whose `cwd` resolves to a project and whose `created_at_ms` is on or after `observe_since`. A spawned thread whose root parent is collected is also collected, whatever its `cwd`.
- **Claude:** list `<claude_projects>/*/`, skipping directories whose mtime is unchanged since the last listing. For each new main `.jsonl`, read records until the first one carrying `cwd` and `timestamp`, then apply the same rule. The result is cached by device and inode.
- A session that matches more than one rule is collected once. `collection_tasks` stores the set of reasons: `link`, `interval_owner`, `project` and `subagent_of`.

**Folding Codex spawned threads.**

- **Detection:** a thread is spawned when its `source` JSON has `subagent.thread_spawn.parent_thread_id`. Do not rely on `thread_source`: 813 older spawned threads have `thread_source` NULL. `subagent` sources of any other kind (for example `{"subagent":{"other":"guardian"}}`, which has no parent) are independent sessions.
- **One key per thread:** every Codex thread ID is resolved to its **root** by following parents, cached in `codex_roots`, before it enters the registry. That applies whether the ID came from discovery, a creator link, an assignee or an interval owner. A spawned thread is collected only as `thread=<root>, agent=<child>`, never as its own task. So `responses` never sees one response under two keys.
- **Storage:** the child's rollout is a second file of the root's task, `sources(task=<root>, file="codex-agent-<child>")`, with its own cursor. The Codex `session_meta` identity check compares line 1 with the **child** ID for such files. `request_detail.agent` holds the child ID for Codex rows; cost plan §5.8 had NULL there.
- **Display:** its spend belongs to the root, it has no card of its own, and the breakdowns show it as a subagent.
- **Attribution:**
  - A child's request uses the **child's own** ownership intervals when the child has any.
  - Otherwise it uses its root's intervals.
  - Today no bead names a spawned thread, so the root's intervals always apply. The child-interval case still keeps a bead that a subagent claims from showing $0.
  - This stops a warden spawned from an executor from being charged as unowned.
- **Migration:** step 1 moves any already-stored child task, one collected through a link, under its root. The `hive cost --bead` `not_captured` text changes to say that spawned reviewers are now included.

**Cost-plan amendments**, all made in step 1:

- §5.2: the identity check for child files.
- §5.4: the child file key.
- §5.8: the Codex `agent` column.
- §5.9, and the cost invariant sentence. "A request is charged to a bead only when its thread held that bead's ownership interval" becomes:

> …only when its thread held that bead's ownership interval, or, for a Codex spawned thread without intervals of its own, when its root parent thread did…

**Retention guard.** Cost plan §5.7 keeps OTLP events for *unlinked* sessions for 7 days. A project session counts as linked for that rule. The retention guard there, which skips retention on `registry_error`, also skips retention when Codex or Claude discovery failed in that sweep. Otherwise a failed discovery would make project sessions look unlinked and their events would be deleted.

**Invariant text** (`docs/invariants.md`), changed in step 1:

> Observation derives thread IDs from bead creator metadata, native assignee fields, bead ownership-interval owners recorded in the Beads events table, Codex spawned threads of collected threads, and, for projects with `observe_since`, sessions whose working directory belongs to the project repository and which started on or after that date.

### 4.2 Roles

Roles are inferred from records the hosts already write. Agents write nothing new.

- **Claude main thread:** a user record containing `<command-name>/<skill></command-name>`, and each assistant line's `attributionSkill` (cost plan §6).
- **Claude subagent:** its own `attributionSkill`, otherwise a role name in its `meta.json` description (for example "Cold warden review of hv-4up" gives warden).
- **Codex:** the first user message mentioning `$<skill>` or `/<skill>`. For a spawned thread, the spawn message text, then `threads.agent_role` (`worker`, `explorer` and `default` are not roles and map to "subagent of <parent role>").
- **Fallback:** the leading emoji of the thread title, from Claude's latest `custom-title` record or Codex `threads.title`. The emoji map comes from `skills/shared/entry.md`.
- Skill names are read from `skills/*/SKILL.md` in the selected source, not hard-coded.

Stored as `role_spans(thread, agent, role, start)`. Every request gets a **request role**:

- the role of the span covering its `(thread, agent, observed_at)`;
- for a subagent without its own role, its parent's role at spawn time, marked `inherited`;
- `ad_hoc` if nothing matches.

A session's **primary role**, used for its emblem, is its first main-thread role. All spend splits, including the summary strip and hotspots, use request roles. So a warden subagent inside an executor counts as warden.

### 4.3 Tool calls and session events

The collector already reads every transcript byte incrementally. Step 2 adds small metadata rows during the same pass. **No transcript text is stored.** Every stored field is an identifier, enum, number, timestamp or hash.

`tool_calls(host, thread, agent, call_id, tool, started_at, finished_at, duration_ms, status, input_hash8, input_bytes, result_bytes, file, use_offset, result_offset)`, where `status` is `ok`, `error`, `pending` or `orphaned`:

- **Claude:** pair `tool_use` blocks with `tool_result` blocks by ID. `error` comes from the result's `is_error`. Duration is the result record's timestamp minus the use record's.
- **Codex `function_call`:** pair it with `function_call_output` by `call_id`.
- **Codex `custom_tool_call` `exec`:**
  - The input is a script, and the output is `Script completed / Wall time N seconds / Output:` followed by JSON chunks.
  - Each chunk carries its own `exit_code` and `wall_time_seconds`.
  - Step 2 parses the chunks into `tool_commands(call_id, ordinal, exit_code, wall_ms, command_hash8, command_kind)`. `command_kind` is a classifier result such as `tg_wait`, `tg_candidate`, `sleep`, `test` or `other`, from a fixed prefix list.
  - A call is `error` when any chunk has a non-zero `exit_code`.
  - Output that cannot be parsed marks the call `unparsed`. That call gets no command rows and still has a duration.
- A pending call still unmatched when its thread has been idle for 1 hour becomes `orphaned`.

`session_events(host, thread, agent, kind, at, until, amount_picos, ref, file, offset)`. `ref` is a tool-call ID, request ID or enum, never text. `kind` is one of:

| Kind | Source |
|---|---|
| `api_error` | Claude `system` records with subtype `api_error` (14 seen locally). |
| `interrupt` | User text starting `[Request interrupted by user` (Claude); `turn_aborted` events (Codex). |
| `permission_denied` | A tool result starting with the Claude permission-denial text (3 seen). |
| `human_wait` | `AskUserQuestion` calls, from use to result (45 seen). Permission-prompt waits are unverified; see acceptance item 5. |
| `idle` | A gap over 5 minutes between a turn's last record and the next user record. |
| `compaction` | `compact_boundary` / `microcompact_boundary` system records, plus cost plan `context_resets`. None seen locally; the fixture follows the documented shape. |
| `cache_rewrite` | A request with `cache_read < P(k−1)` after an idle gap longer than the TTL written in the prompt. `amount_picos` is the cost of the rewritten prefix at write rate minus the same tokens at read rate. The inputs come from cost plan §7.5. |
| `role_change` | From §4.2. |

**Derived at report time:**

- **Retry loop:** three or more consecutive errors of the same tool with equal `input_hash8` (or `command_hash8`).
- **Slow:** above both 60 seconds and the 95th percentile of that tool's or command kind's duration in the project over 30 days.
- **Waiting:** a call whose command kind is `tg_wait`, `sleep`, or a Monitor-type tool. It is never counted as slow.

### 4.4 Tollgate candidates

Hive reads Tollgate only through `tg --json --no-launch`, never its database. Projects map to Tollgate repository IDs through `tg --json repo list`, matching `state.path` to the project repository. The mapping is cached, and an unmapped project has no CI panel.

**Join (user decision):**

1. **Primary:** candidate IDs that appear in a thread's `tg` tool calls or results.
   - For Claude, the input and result of Bash calls starting with `tg`.
   - For Codex, `exec` chunks with command kind `tg_*`.
   - The collector scans only those blocks for UUIDs that `tg status` confirms. It records `(thread, agent, candidate_id, first_seen_at)`.
   - The candidate is assigned to a bead by the ownership-interval rule, at `first_seen_at`. For folded Codex subagents that means their root parent's intervals.
2. **Fallback:** the candidate's `metadata.branch` equals the bead ID or starts with `<bead-id>-`. It is shown as "matched by branch".
3. If neither applies, the candidate appears only on its thread's card.

**Polling.** The collector calls `tg --json --no-launch --repository <id> status <candidate>` for each non-terminal joined candidate, oldest first, within its budget (§8.6). It stores the terminal record once in `tollgate_candidates`:

- attempt states;
- step names, `result_class`, `exit_code` and `elapsed_ms`;
- start and finish times;
- merge-conflict or cancellation reason enums.

Tollgate timestamps are arrays `[year, day_of_year, hour, minute, second, nanosecond, offset_h, offset_m, offset_s]`. Step 3 decodes them with a checked parser, and an unexpected shape is a gap. Logs are never copied (§7.3). If Tollgate is unavailable, `tollgate_unavailable` appears in status and cached rows stay.

## 5. The feed

### 5.1 Cards and the accounting identity

Every card is keyed by a stable identity: `bead:<id>`, `session:<thread>` or `ledger:<kind>:<project>`. A session's card keeps its key when it changes from agent to tail, so paging cursors stay valid.

| Card | Contains | Amount |
|---|---|---|
| **Bead** | every bead in `bd list`, plus any bead ID with attributed or cached intervals that is no longer listed (deleted or renamed, flagged as in cost plan §5.9) | `attributed_usd` |
| **Agent** | a collected root session that never owned an interval | its `unowned_usd` (its total minus any `unattributable_usd`, which sits on the ledger) |
| **Tail** | a root session that owned intervals and whose `unowned_usd` is at least **$1.00** or **25%** of its total | its `unowned_usd` |
| **Ledger: small tails** | per project, the `unowned_usd` of owning sessions below the tail threshold | sum |
| **Ledger: unattributable** | per project of the `interval_unknown` bead (its `hive_project`, else "Other"), the `unattributable_usd` overlapping it, linking to those beads and threads | sum |

- **Amounts are lifetime** for each card. The time window affects only the summary strip, the hotspots and an "active in window" filter.
- **Project of a card:**
  - A bead card uses its `hive_project`.
  - A session card uses the project its `cwd` resolves to.
  - A linked session outside every registered project, and a bead without `hive_project`, go under an **"Other"** project group.
- **Identity (step 4 test, and acceptance item 1).** For requests with a stored quote, and the Codex tier `standard`, the sum of bead card amounts, agent card amounts, tail card amounts and both ledgers equals the sum of thread totals. That is exactly the cost plan's `hive cost --reconcile`. The test uses `--reconcile` as its oracle. Unpriced requests are counted separately, as there.
- A shared request (cost plan: equal split among beads held at once) contributes its share to each bead.

### 5.2 Card content

Modelled on the Fulcrum work card, with Hive content:

```text
[emblem] battlement            (● Working)
Recover interrupted startup
hv-4up · Implementation ready for review.
───────────────────────────────────────────
$3.42 · ⚒️ Executor · 🛡️ Warden   ⚠ 2 CI  ✕ 5 tool errors  ⏱ 1   >
```

- **Emblem:** the project emblem on bead cards, the primary-role emblem on agent and tail cards.
- **Title:** the bead title. For session cards, the session title (Claude `custom-title`, Codex `threads.title`), or else the first line of the first user prompt, capped at 120 characters and read at API time from the transcript like an excerpt (§7.3).
- **Subtitle:** the bead's close reason, notes or description (first line, whichever exists first). For session cards, the primary role and project.
- **Footer:**
  - the amount, prefixed `≥` when any contributing thread lacks a complete estimate;
  - the request roles in order of spend;
  - badges for CI failures, tool errors, slow tools, API errors, and human wait over 10 minutes.

All text is rendered as React text nodes. No field is inserted as HTML (§7.1 for Markdown).

### 5.3 States

Time-based states are computed when the feed is read, not stored. An owner's activity time is the newest modification time across **all** of its root session's files: the main transcript, Claude subagent files and folded Codex child rollouts. So a parent waiting on a 40-minute review subagent is Working, not Stalled. The API `stat`s only a bounded set: threads with an open interval or activity in the last 24 hours. So these states are fresh even when collection lags.

| Chip | Rule |
|---|---|
| Working (success) | `in_progress`, and the owner's transcript was modified less than 5 minutes ago |
| In CI (information) | a joined candidate is queued or running |
| Stalled (attention) | `in_progress`, owner idle for 30 minutes or more, and not In CI |
| Ready (violet) | `open`, no open blockers |
| Blocked | `open` with an open blocker |
| Awaiting approval | `deferred` without a deferral date |
| Deferred | `deferred` with a date |
| Complete (success) | `closed`, `hive_resolution=completed` |
| Cancelled (secondary) | `closed`, `hive_resolution=cancelled` |
| Needs attention (failure) | overrides the chips above when the latest joined candidate failed and was not superseded, or the bead is `interval_unknown` or deleted |

Agent and tail cards are Working, Idle, or Finished (no modification for 24 hours).

### 5.4 Order, filters and paging

- Cards are sorted by last activity, newest first. Last activity is the latest bead event, request, tool call or candidate update. Ties are broken by card key.
- Filters: text, project, role (any request role), state, active in window, and "Older completed". Older completed means closed more than 7 days ago; those cards are hidden by default, as in Fulcrum.
- 50 cards per page, with a keyset cursor on `(last_activity, key)`.
- While the tab is visible, the page polls every 15 seconds. It sends `If-None-Match` with the feed `revision`, so an unchanged feed costs one small response.

### 5.5 Summary strip and hotspots

- Windows are Today, 7 days and 30 days, in the Mac's local time zone. The API reports the zone.
- Window totals come from request rows, using `observed_at` in the window and the Codex tier `standard`. They are split by request role, with a coverage caveat when any thread in the window lacks a complete estimate.
- The **hotspot banner** shows one item at a time, from deterministic rules. The highest dollar impact comes first, and arrows step through the rest. Each item links to the cards behind it. Initial rules:
  1. **Review share:** warden request-role spend over 35% of executor request-role spend in the window.
  2. **CI churn:** a bead with 3 or more failed attempts on the same step.
  3. **Idle cache rewrites:** `cache_rewrite` amounts over $1 in the window.
  4. **Retry loops:** each retry loop, ranked by spend during the loop.
  5. **Stalls:** in-progress beads stalled for more than 2 hours.
  6. **Unowned spend:** tail plus small-tail spend over 20% of the window. The fix is to claim earlier.
  7. **Coverage:** threads without a complete estimate, when their spend exceeds 10% of the window.
- The rules live in one Python module with black-box tests. Their thresholds are constants there.

## 6. Visual design

### 6.1 Tokens

Taken from the Fulcrum primitives, as CSS custom properties:

| Token | Value | Use |
|---|---|---|
| `--canvas` | `#10101A` | page background |
| `--card` | `#181826` | cards |
| `--raised` | `#222235` | hover, selected, popovers |
| `--text-primary` | `#F2F0FA` | titles |
| `--text-secondary` | `#A6A4B8` | subtitles, labels |
| `--violet` | `#C4B5FD` | brand, focus, Ready |
| `--attention` | `#F3C969` | Stalled, awaiting approval |
| `--success` | `#6EE7B7` | Working, Complete |
| `--information` | `#93C5FD` | In CI |
| `--failure` | `#FDA4AF` | Needs attention, errors |

- **Typography:** 28, 18, 16 and 14 px, semibold for headings, and a monospace face for bead IDs, thread IDs and amounts.
- **Spacing:** a scale of 4, 8, 12, 16, 24, 32 and 48. Desktop padding is 24, narrow padding 16.
- **Shape:** cards have radius 16, controls 10. The focus ring is 2 px violet with a 2 px offset.
- Colour is always paired with a label or symbol, as the reference states.
- Dark only in v1. The tokens are defined so that a light theme can be added.
- Chart series colours come from a validated categorical palette derived from these tokens, following the dataviz guidance.

### 6.2 Brand and emblems

- Brand mark: a hexagon outline with a smaller inner hexagon, replacing Fulcrum's diamond. The wordmark is "Hive".
- Role emblems are single-weight violet line icons. Each is an inline SVG component on a 32 px grid, based on the role's emoji:

| Role | Emblem |
|---|---|
| Executor | hammer (from ⚒️) |
| Bead | a short strand of three beads |
| Warden | shield |
| Weaver | spool with a thread |
| Sage | open book |
| Mason | trowel over two bricks |
| Vizier | crystal orb on a stand |
| Justiciar | flame |
| Archivist | archive box |
| Ad hoc | a dot in a circle |
| Subagent | the role's emblem, smaller, with a branch mark |

- Project emblems are a hexagon badge with the project's initial. "Other" uses an empty hexagon.
- Fulcrum's names (Archon, Overseer, Watchman, Inquisitor) are not used.

### 6.3 Layout

- **Desktop:** a left rail (brand, "Newsfeed", and the project list with counts), then the top summary strip, the hotspot banner, the filter row and a 3-column card grid.
- **Narrower widths:** 2 columns below 1024 px. Below 640 px, 1 column, and the rail becomes a compact header. The dashboard is loopback only (§8.4), so narrow layouts serve small windows, not phones.
- The detail view is a routed full page (`/bead/hv-4up`, `/session/<id>`, `/ledger/<kind>/<project>`), not a modal, so it can be linked and reloaded. Back returns to the feed at its scroll position.

**Mockups.** Step 6 begins with static mockups to check the approved visual direction before components are built, without an intermediate sign-off pause. They cover the feed, bead detail, agent detail, and the empty, building and error states.

## 7. Detail view

### 7.1 Bead detail

From top to bottom:

1. **Header:** emblem, title, ID, state chip, project, amount with a coverage note, and owner chips.
2. **Timeline chart:** time runs from the bead's creation to its close, or to now.
   - A cumulative spend line, coloured by request role.
   - Ownership intervals as bands under the axis, labelled with role and host.
   - **Context:** within 2 hours of an interval edge, the same thread's unowned spend is drawn faintly and labelled "outside ownership — not in total". When edges of two beads compete for the same unowned span, the span goes to the nearer edge.
   - Markers: ✕ tool error, ⏱ slow tool, ↻ retry loop, CI attempt (pass, fail or conflict), ⚡ API error, ⤓ compaction, ⟳ cache rewrite, ✋ interrupt or denial.
   - Shaded spans for human wait, idle time and Tollgate running.
   - Brushing a time range filters the tables below.
3. **Breakdown bar:** one horizontal stacked bar with a dimension switcher. The dimensions are request role, thread, subagent, model, token category, skill, query source and tool. Token categories are uncached input, 5-minute cache write, 1-hour cache write, cache read, output and server tools. The tool dimension is labelled "estimated".
   - The API aggregates the bead's request rows itself, in exact integer picodollars. It does not rely on `BeadCost`'s `by_*` arrays, which lack several of these dimensions.
   - To do this, step 4 adds `hive cost --bead ID --requests`, which pages `request_detail` rows with `share_picos` for each bead. For a shared request, that is its equal-split share.
   - Tool rows come from the cost plan's `tool_allocation`, scaled by the same share with a largest-remainder split.
   - A step 4 test asserts that every dimension sums to `attributed_usd`.
4. **Diagnostics list:** grouped by kind. Each row shows time, thread or subagent, and duration or USD, with an **Excerpt** button (§7.3).
5. **CI panel:** one entry per candidate, with branch, subject, match method, attempts, each step's result and duration, and the time from first submission to promotion.
6. **Requests table:** the paged bead request rows, sortable by share.
7. **Beads panel (read-only), live from Beads with a 5-second timeout:**
   - `bd show ID --json`: status, priority, type, assignee and owner, created and updated times, description, acceptance criteria, flat metadata as a key-value table, and dependencies.
   - `bd comments ID --json`.
   - Dependents, from `bd dep list`. Step 4 confirms the exact command and fields against bd 1.2.2.
   - The native event history, through a **third fixed query template**. It uses the same validated bead ID as the cost plan's per-bead query and returns `id`, `event_type`, `actor`, and `old_value`/`new_value` truncated to 4 KiB each.
   - Markdown fields are rendered with a sanitizing Markdown renderer. It keeps no raw HTML and allows only `http`, `https` and `mailto` links.
   - A **Commands** menu copies `bd` commands with the full routing prefix and a placeholder actor, never a real thread ID.
   - When Beads is unavailable, the panel shows the collector's cached row (below) with its age.

Step 4 adds a `bead_rows` table that the collector refreshes with each `bd list --all` link refresh. It stores the list fields the feed needs: title, status, priority, `hive_project`, `hive_resolution`, deferral date, close reason, updated time and blocker count. Today `collection_links` keeps only `(task, bead, relation)`.

### 7.2 Session and ledger detail

- **Agent card:** the same timeline and breakdown for the whole root session. The page also lists role spans, subagents (Claude and folded Codex) with their totals, diagnostics, candidates, and the beads the session filed (creator links).
- **Tail card:** the same, restricted to the unowned spans, with the owned intervals drawn as context.
- **Ledger cards:** the contributing threads or beads, with amounts and reasons.

Session-level request rows come from `hive cost --task ID --requests`, filtered by span. The tail breakdown is aggregated from those rows in the same way as §7.1.

### 7.3 Excerpts (user decision: on demand, by offset)

- A stored row has a `file` and a byte offset. The excerpt API reads that one line with the existing chunk reader. It checks that the line still decodes to the same call or record identity, then returns:
  - the tool name and a one-line input summary (command, path or query), at most 300 characters;
  - the first 2 KiB of the result text the model saw. For Claude that is `message.content`, not `toolUseResult`, as in cost plan §7.3. For a Codex `exec` call, it is the failing chunk's output.
- The excerpt is `excerpt_unavailable`, with a reason, when:
  - the file was replaced, truncated or deleted;
  - the line is over 256 KiB (`MAX_LINE`);
  - the result was stored in Claude's `tool-results/*.txt` (a pointer is shown instead);
  - a Codex rollout moved on archive and the index path no longer matches.
- **CI logs:** `tg --json logs <id> --step <name>` is read with a 1 MiB cap and a 5-second timeout. The last 4 KiB are returned; if the cap is hit, the result says it was truncated.
- Excerpts and logs are sent with `Cache-Control: no-store`. They are never cached, logged or stored.
- A "Copy session ID" action is offered. Resuming or archiving a session remains a native action.

## 8. Architecture

### 8.1 Pieces

```text
browser ──HTTP──▶ hive dashboard serve        resident; routing + static files only
                     │ GET /                  → UI tree of current master; built? serve index.html : spawn build
                     │ GET /c/<tree>/…        → static file from dashboard-builds/<tree>/
                     │ GET /api/v1/…          → spawn source-selected `hive dashboard api …`
                     ▼
     hive dashboard build <sha>               source-selected subprocess; npm + vite in a temp copy
     hive dashboard api <route>               source-selected subprocess; read-only
                     │ telemetry.sqlite3 (read connection), BeadsProcess, tg --json
                     ▼
     hive telemetry watch                     existing resident collector; adds §4 rows and §8.6 work
```

### 8.2 The resident server (invariant change)

`hive dashboard serve [--port 4320]` is a new resident command, separate from the collector (user decision). It follows the watcher's pattern. The launcher source-selects it at start, and its resident module (`hive.dashboard_transport`) keeps running from that snapshot. So changes to that module need a restart, and the module is kept as small as possible:

- It binds `127.0.0.1` only and accepts only `GET` and `HEAD`.
- **Request checks**, all before any work:
  - `Host` must be `127.0.0.1:<port>` or `localhost:<port>`. That blocks DNS rebinding.
  - `Sec-Fetch-Site` must be `same-origin` or `none` when present. That blocks cross-site `<img>`/`<script>` GETs, which could otherwise start builds or use up API slots. Requests without the header, such as `curl`, are allowed; they come from local processes that could read the database anyway.
- **Response headers:**
  - `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - no CORS headers
  - `Cache-Control: no-store` on everything except hashed static assets
- **Static route:**
  - `<tree>` must match `^[0-9a-f]{40}$` or `^[0-9a-f]{64}$`.
  - The remainder is percent-decoded exactly once. After decoding, it may contain only `[A-Za-z0-9._/-]`, with no `..` segment and no empty segment.
  - The file is opened relative to the build root with `O_NOFOLLOW` on every component. The resolved path is then checked to be inside `dashboard-builds/<tree>/`.
  - A MIME type comes from a fixed extension table; anything else is 404.
- **Client routes:** `GET` on `/bead/<bead-id>`, `/session/<uuid>` and `/ledger/<kind>/<project-id>` returns the same `index.html` as `/`, after the same validation. Every other path is 404. That makes detail pages linkable and reloadable (§6.3).
- **API route:** a fixed table maps each path to an argv. Path segments are validated (bead ID pattern, UUID, cursor token) before use. Free text is passed as `--q=<text>`, so a leading `-` cannot become an option, and never through a shell.
- **Limits:**
  - 16 concurrent connections, with headers due within 5 seconds.
  - 4 concurrent API subprocesses; waiting requests time out after 10 seconds with 503.
  - A 10-second timeout for each API subprocess (504).
  - One build subprocess at a time.
- If the port cannot be bound, the command exits non-zero with a clear message.

**Invariant text** (`docs/invariants.md`), changed in step 5:

> …A resident observer retains only timer and connection continuity. A resident dashboard server retains only loopback HTTP routing and static file serving. It builds and answers every API request through new source-selected calls.

### 8.3 Build on demand (user decision)

The UI is React + Vite + TypeScript, like Tollgate's, in `dashboard/`. Built output is not committed.

**Builds are keyed by the UI tree, not the commit.** `tree = git rev-parse <master>:dashboard`. A Python-only commit reuses the existing build, so it causes no rebuild and no reload pill.

On `GET /`:

1. The server reads the current master commit (`hive_bootstrap.source.current_commit`) and its `dashboard` tree hash.
2. If `dashboard-builds/<tree>/index.html` exists, the server returns it.
3. If the tree has a failure marker:
   - For `dashboard-builds/<tree>.failed`, the build is not retried. This marker is written only when Vite or TypeScript rejects the code, so only a new UI tree can fix it.
   - For `dashboard-builds/<tree>.retry-after`, the build is retried after the time the marker records. This marker is written for environment failures: `npm ci` or network errors, a missing or wrong Node, or the timeout. Retries back off from 1 minute, doubling up to 1 hour. `hive dashboard build <sha> --retry` clears either marker.
   - Meanwhile the server serves the newest successful build with a banner "UI build for <tree> failed — showing <older tree>" and the log path. If no successful build exists, it serves a plain error page with the log path and, for a retryable failure, the next retry time.
4. Otherwise the server starts the source-selected `hive dashboard build <sha>` (if one isn't already running) and returns a small self-refreshing "Building dashboard…" page. That page is inline HTML with no scripts, and it uses a `<meta http-equiv="refresh">` refresh.

`hive dashboard build <sha>` runs from the selected source. It never writes into the immutable `${state}/sources/<sha>`:

1. Take the exclusive lock `dashboard-builds/<tree>.lock`, then re-check whether the build exists.
2. Dependencies:
   - Compute `lock = sha256(dashboard/package-lock.json)`.
   - Under its own lock `dashboard-node/<lock>.lock`, ensure `dashboard-node/<lock>/node_modules` exists. If it doesn't, copy `package.json` and `package-lock.json` into a temp directory, run `npm ci --ignore-scripts` there, and rename the result into place.
   - This install needs network access. Its failure is a build failure with the npm log.
3. Extract `git archive <sha> dashboard` into a temp directory under `dashboard-builds/tmp/`, and symlink `node_modules` into that copy. Node and Vite then resolve packages normally, and the config's temp files and `tsbuildinfo` stay in the copy.
4. Run `node_modules/.bin/vite build --base /c/<tree>/ --outDir <tmp-out>`, with a 180-second timeout and a log at `dashboard-builds/<tree>.log`. The Vite config sets `cacheDir` inside the temp copy, so nothing writes into the shared `node_modules`.
5. On success, rename `<tmp-out>` to `dashboard-builds/<tree>/` atomically. On failure, write the marker for the failure's class (step 3). Remove the temp copy in both cases.

More build rules:

- **Node** must be installed; its version is pinned in `dashboard/.nvmrc`. A missing or wrong Node is a build failure that names the version needed. `scripts/prepare-check` checks it too.
- **Code splitting is off.** Vite builds one JS and one CSS file, and dynamic imports are inlined. So an open tab never needs a chunk that pruning has removed.
- **Pruning:** after a successful build, delete builds that are neither among the newest 5 nor used in the last 24 hours. Also delete `dashboard-node/<lock>` directories that no remaining build used, and `.failed` markers older than 7 days.
- **Reset:** stop the server and delete `dashboard-builds/` and `dashboard-node/`.

**API/UI skew.** API calls always run the *current* master. Every API response carries `schema` (an integer) and `ui_tree`, the current master's `dashboard` tree. The page compares `ui_tree` with the tree it was served from. If they differ, it shows a "New version — reload" pill. If `schema` is one the page does not know, the page shows only that pill.

### 8.4 Access

Loopback only, no authentication, desktop-first (user decision). Anyone who can open a loopback socket as this user can read the dashboard, which matches the trust level of the transcripts themselves. The `Sec-Fetch-Site` check keeps other websites in the user's browser out. Remote access is a follow-up that needs authentication.

### 8.5 The API

`hive dashboard api <route> [args] --json` is an ordinary CLI command, so it can be tested black-box and used without the server. Every response has `code`, `schema`, `source_commit`, `ui_tree` and `generated_at`.

| Route | Returns |
|---|---|
| `feed --window 7d [--project P] [--role R] [--state S] [--q=TEXT] [--active] [--older-completed] [--cursor C]` | summary strip, hotspots, one page of cards, `revision`, collector freshness |
| `bead ID` | bead detail: Beads panel, intervals, timeline series, dimension totals, diagnostics, candidates |
| `bead ID --requests --cursor C` | bead request rows with `share_picos` |
| `session ID [--tail]` | session or tail detail |
| `ledger KIND PROJECT` | ledger detail |
| `excerpt --thread T --call ID` / `--event ID` | §7.3 excerpt |
| `ci-log --candidate ID --step NAME` | log tail |

**Performance.** The target is a feed response under 500 ms with 1,000 beads and 5,000 sessions. To meet it:

- The collector maintains `card_summaries`, one row per card key: lifetime amount, coverage flag, counts per diagnostic kind, last activity, request-role totals and project. The feed reads that table, `bead_rows`, and the bounded `stat` set from §5.3.
- A summary row is recomputed when any of these touch it:
  - new requests, tool calls, events or candidates on its threads;
  - an interval rebuild, an `interval_unknown` change, or a rename or delete of its bead;
  - a `bead_rows` change;
  - a threshold crossing between agent and tail.
- Window totals come from an indexed `observed_at` scan of request rows.
- Detail routes compute on demand from the cost plan's views.

All API reads use read-only SQLite connections, so they never contend for the collector's write lock. A store that is not yet migrated returns the cost plan's structured "store not yet migrated" error, and the page shows it as a page state.

### 8.6 Collector scheduling and budget

Project sessions greatly increase the thread count. Today `CollectionRegistry.next` picks the least recently attempted threads, 32 per sweep. At 5,000 threads a full cycle would take tens of minutes. Step 1 changes scheduling to be **activity-first**:

1. Threads whose source files changed size or mtime since their last pass, newest change first. This is checked with `stat`, which is cheap. It also covers Claude subagent files and folded Codex subagents.
2. Then threads never attempted.
3. Then idle threads, least recently attempted first, limited to 4 per sweep.

The collector's 5-second deadline is already divided by the cost plan: up to 2 seconds for Beads events and links, and up to 2 seconds for OTLP ingest. Those are **caps used only while behind**. In steady state each takes milliseconds. This plan runs its work in a fixed priority order within that deadline:

| Order | Work | Budget |
|---|---|---|
| 1 | transcripts of changed threads | exactly 1 s while Beads events or OTLP ingest is behind; otherwise everything up to 0.8 s before the deadline |
| 2 | Beads events and links (cost plan) | up to 2 s |
| 3 | OTLP ingest (cost plan) | up to 2 s |
| 4 | discovery (§4.1) | up to 0.3 s, from what remains |
| 5 | Tollgate polling (§4.4) | up to 0.3 s, from what remains |
| 6 | `card_summaries` refresh | up to 0.2 s, from what remains |
| 7 | idle threads | whatever remains |

- In the worst case, orders 1–3 use the whole 5 seconds (1 + 2 + 2).
- **Orders 4–7 can starve** while the cost plan's work catches up. That is deliberate: attribution and retention correctness come before dashboard freshness.
- In steady state, orders 2 and 3 take milliseconds, and orders 4–6 always have their 0.8 s.
- Work that runs out of time resumes on the next sweep. Collector status adds `discovery_behind`, `tollgate_behind` and `summaries_behind`, and the feed header shows collector freshness. Time-based card states don't depend on this (§5.3).

## 9. Failure modes

| Situation | Behaviour |
|---|---|
| Collector not running | The feed renders from the last sweep, and the header shows "Collector last swept 3 h ago". Working and Stalled still come from file `stat`s. |
| Collector behind | Freshness notes from the `*_behind` flags. |
| Beads server down | Bead fields come from `bead_rows` with their age; the detail view shows "Beads unavailable". |
| Tollgate unavailable | Cached candidate rows stay, with a "Tollgate unavailable" note. No badges are removed. |
| Discovery failed | That pass collects no new sessions. OTLP retention is skipped (§4.1). |
| Transcript deleted or rewritten | Costs remain, because they are stored. Excerpts and fallback session titles become unavailable. |
| Incomplete coverage | The amount is shown with `≥`, and a tooltip lists the cost plan's coverage flags. |
| `interval_unknown` bead | Needs attention chip. Its spend sits on the unattributable ledger card, never on the bead. |
| Deleted or renamed bead | A bead card from cached intervals, flagged "deleted" or "renamed-or-deleted". |
| UI build fails | The previous build is served with a banner. With no previous build, an error page. The failure is not retried until the tree changes. |
| `npm ci` without network | A build failure with the npm log. The previous build is served. |
| UI older than API | Reload pill. An unknown schema blocks rendering. |
| API subprocess timeout or saturation | 504 or 503 with a retry button. The server keeps running. |
| `hive.json` gains `observe_since` before step 1 | Every `hive` call fails on the unknown key. Operations warns against this. |

## 10. Delivery plan

Each step is one Conventional Commit, delivered through Tollgate with a fresh cold review, per `AGENTS.md`. Each step updates `docs/implementation.md`, and `docs/operations.md` where it applies.

1. `feat(observe): collect sessions in registered projects`
   - `observe_since` in bootstrap settings, with the project list passed to the application.
   - Git-common-dir project resolution and its cache.
   - Incremental Codex and Claude discovery.
   - Folding Codex spawned threads, with root-parent attribution (the cost-plan §5.9 wording).
   - Reason sets in `collection_tasks` and activity-first scheduling with the §8.6 budget order.
   - The discovery-failure retention guard and the invariant text.
   - Black-box tests, with a fixture index and projects root:
     - matching, sibling-prefix, Tollgate worktree, Codex worktree, removed worktree and pre-cutoff sessions;
     - a spawned Codex child attributed through its parent's interval, and a child with its own interval attributed through that;
     - a spawned ID arriving as a creator link collected once, under its root;
     - a pre-existing stored child task migrated under its root;
     - an older spawned thread with NULL `thread_source`, and an `other`-kind subagent kept independent;
     - a changed thread scheduled ahead of 100 idle ones;
     - discovery failure skipping retention.
2. `feat(telemetry): record tool calls, session events and roles`
   - The §4.2 and §4.3 tables, including Codex `exec` chunk parsing, slow and waiting classification, and retry-loop derivation.
   - Fixtures for each event kind on both hosts, and an unparsable `exec` output.
   - A test asserting that no stored column holds free text.
3. `feat(telemetry): observe Tollgate candidates`
   - Candidate mentions, branch fallback, repository mapping, timestamp decoding, polling budget and terminal cache.
   - Tests use a fake `tg` on `PATH` that returns recorded JSON, including an unknown timestamp shape.
4. `feat(dashboard): feed and detail API`
   - `bead_rows`, `card_summaries`, card kinds and ledgers, states, and the hotspot rules.
   - `hive cost --bead --requests` with `share_picos`, and dimension aggregation.
   - The third events query template, `bd show`/comments/dependents reads, the excerpt reader and every §8.5 route.
   - Black-box tests:
     - the §5.1 identity against `hive cost --reconcile`, including an `interval_unknown` bead whose assignee never owned a known interval (no double count on its agent card), a deleted bead, an outside-project linked session, a shared request and a below-threshold tail;
     - every breakdown dimension summing to `attributed_usd`;
     - a stable card key across agent→tail.
5. `feat(dashboard): serve the dashboard on loopback`
   - The resident server, request checks, headers, static-path hardening and limits.
   - `hive dashboard build`, with the temp copy, node_modules cache, `--base`, failure markers and pruning.
   - Invariant text, plus operations: start, stop, port, Node requirement, network for `npm ci`, and reset.
   - Tests use a **fixture `dashboard/`** and fake `npm` and `vite` executables on `PATH` that write known files, so they need no real Node. They check:
     - rejection of foreign `Host`, cross-site `Sec-Fetch-Site`, `POST`, encoded `..` and symlinked paths;
     - two concurrent requests for one tree start one build;
     - a code failure falls back and is not retried; an environment failure retries after backoff; `--retry` clears both;
     - a reload of `/bead/<id>` returns `index.html`, and an unknown path returns 404;
     - a Python-only commit reuses the build;
     - a UI commit is served on the next load without restart;
     - subprocess timeout and saturation.
6. `feat(dashboard): design system and feed`
   - Mockups first, checked against the approved references. Then the `dashboard/` React app: tokens, brand, emblems, cards, filters, summary strip, hotspot banner and polling.
   - Vitest component tests. `tsc -b`, ESLint and `vitest run` join `scripts/check-fast` and `scripts/check`.
   - `scripts/prepare-check` runs `npm ci` in the worktree. So Tollgate's CI step needs Node 20 or newer and network access whenever `package-lock.json` changes; this is documented in operations.
7. `feat(dashboard): bead, session and ledger detail`
   - Timeline, breakdown switcher, diagnostics, excerpts, CI panel and requests table.
   - The read-only Beads panel, with sanitized Markdown and copyable commands.
   - A Vitest test renders hostile bead text (`<script>`, `javascript:` links, raw HTML) and asserts none of it becomes live markup.
8. `docs: record dashboard acceptance`. `docs/acceptance.md` evidence (§11) and `docs/implementation.md`.

Steps 1–3 are collection work and can land before any UI. Step 4 needs steps 1–3. Step 5 can proceed in parallel with step 4, since it uses fixtures. Steps 6 and 7 need steps 4 and 5.

## 11. Validation and acceptance

Automated, in `scripts/check`: all step tests above, plus the TypeScript, lint and Vitest gates.

Manual acceptance is recorded in `docs/acceptance.md`. Browser checks use the Playwright MCP service, with accessibility snapshots first and screenshots for visual review.

1. **Identity.** On the live store, the sum of all cards, including the ledgers, equals the `hive cost --reconcile` thread total exactly. The 7-day strip total equals the sum of request rows in the window.
2. **Known bead.** `hv-4up`'s detail shows the Codex `01a0d0bb` and Claude `1adb320e` intervals. Its amount equals `hive cost --bead hv-4up`'s `attributed_usd`.
3. **Project sessions.** With `observe_since` set for Hive:
   - a fresh ad-hoc session in the Hive repository appears as an agent card within two sweeps;
   - a session in a Tollgate worktree and one in a Codex worktree are both attributed to Hive;
   - a sibling-path session is not;
   - a spawned Codex reviewer appears as a subagent of its parent, not as its own card.
4. **Roles.** Sessions for at least five different roles show the right emblem. A warden subagent inside an executor counts as warden in the strip.
5. **Diagnostics.** A disposable session with a deliberately failing tool call, a `sleep 70` call, an `AskUserQuestion` wait, a permission prompt and an interrupt shows each marker at the right time, with a working excerpt. Acceptance records whether permission-prompt waits are visible in the transcript.
6. **CI.** A disposable bead whose candidate fails once and then passes shows both attempts, the failing step and the log tail. A candidate matched only by branch is labelled that way.
7. **Hot reload.** With the server running and a page open:
   - promote a Python-only commit: there is no rebuild and no pill;
   - promote a UI commit: the pill appears, and reload serves the new build without a server restart;
   - promote a commit that breaks the build: the previous build is served with the banner.
8. **Security.** These are rejected: a foreign `Host`, a cross-site page embedding `<img src="http://127.0.0.1:4320/">` (no build starts), `POST`, an encoded traversal, and an invalid bead ID. No response carries CORS headers.
9. **Performance.** The feed responds in under 500 ms on the live store and a cold detail view in under 2 seconds. With every project session collected, a changed thread's new requests appear within two sweeps.
10. **Visual review.** A `visual-review` pass at 1440, 1024 and 600 px has no open bugs, and the implementation is checked against the Fulcrum references.

## 12. Beads

Epic **`hv-762` — Deliver the Hive web dashboard** owns this plan. Its native `parent-child` links include the completed design task `hv-amo` and these implementation tasks, retaining their existing IDs:

- `hv-ijd`: collect project sessions and diagnostic metadata (steps 1–2).
- `hv-q9z`: observe Tollgate candidates (step 3).
- `hv-l85`: dashboard API and loopback server (steps 4–5).
- `hv-286`: dashboard UI and acceptance (steps 6–8).

The epic tracks the six cost tasks below; implementation children retain their step-order dependencies and the completed technical fixes `hv-k8r` and `hv-82b`:

| Prerequisite | Required outcome |
| --- | --- |
| `hv-b4r` | Claude thread pricing, request detail, and exact subagent/skill breakdowns, with the cost plan's required evidence. |
| `hv-2uo` | Request-event collection, transcript joins, coverage, and event-source acceptance. |
| `hv-qg7` | Historical-owner collection and per-bead attribution with exact reconciliation. |
| `hv-8ib` | Estimated tool allocation, with empirical `byte_split_error` evidence reported when available and uncertainty disclosed. |
| `hv-k8r` | One event timestamp for event-only request details and bead attribution, including ownership-boundary regression coverage. |
| `hv-82b` | The queryable per-request `tool_allocation` rows promised by cost-plan §5.8, with exact per-request reconciliation against retained prices. |

The epic uses `tracks` links for cost work. On the user's 2026-09-25 resumption, the acceptance-only blocking edges from `hv-b4r`, `hv-2uo`, `hv-qg7` and `hv-8ib` to dashboard children were removed. Their code is delivered; outstanding evidence remains recorded on those tasks without pretending it passed. Parent-child links group the dashboard work, and technical dependencies preserve its execution order. The epic completes when its four implementation tasks and this plan's executable checks are delivered, with evidence and limitations recorded.

The [cost acceptance record](acceptance.md#claude-cost-implementation-2026-09-24) distinguishes tested behavior from missing empirical tool-split and live-host evidence. These remain limitations to disclose, not renewed requests for user approval or a reason to stop dashboard implementation.

## 13. Decisions

Decided by the user on 2026-09-24 in the design interview:

1. Bead-less cards cover **all sessions in registered projects**, not only bead-linked threads (§4.1).
2. Collection starts from a per-project **`observe_since`** date (§4.1).
3. The dashboard is served by a **separate resident `hive dashboard serve`** command, not inside the collector (§8.2).
4. The frontend is **React + Vite** (§8.3).
5. Bundles are **built on demand and cached**; no build output goes into git (§8.3). Revision 2 keys the cache by UI tree rather than commit.
6. Cards are beads and never-owning sessions, **plus tail cards** for large unowned spend (§5.1).
7. Roles are **inferred from skill invocations**, with the title emoji as fallback (§4.2).
8. The Beads UI is **read-only with copyable commands** (§2, §7.1).
9. Diagnostics v1 includes **all four groups**: tool errors and slow tools, Tollgate CI, context and cache waste, and human waits and interruptions (§4.3, §4.4).
10. CI join: **transcript candidate IDs first, then a branch-name fallback** (§4.4).
11. The detail view's centerpiece is a **timeline plus a switchable breakdown** (§7.1).
12. Dependency: the **entire cost implementation** supplies the technical foundation; the 2026-09-25 resumption removes acceptance-only approval holds.
13. Fleet view: **summary strip plus rule-based hotspots** (§5.5).
14. Access: **loopback only, desktop-first** (§8.4).
15. Excerpts are read **on demand by offset**; nothing is copied (§7.3).

Chosen by the author, open to change at approval:

- Thresholds: a tail is $1.00 or 25% of the session (§5.1); slow means over 60 seconds and above the p95 (§4.3); stalled after 30 minutes (§5.3); the feed polls every 15 seconds (§5.4).
- Default port 4320. The cost plan's OTLP listener uses 4319.
- Ledger cards for unattributable spend and small tails (§5.1).
- Codex spawned threads are folded into their root parent and attributed through its intervals (§4.1). This extends cost plan §5.9.
- The emblem set (§6.2), checked against the step 6 mockups.

The user approved steps 1–8 on 2026-09-24, with the author's choices above.

Activation resolved by the user on 2026-09-25: observe both `hive` and `battlement` from `2026-09-24`.

## 14. Cold review disposition

A fresh cold review of revision 1 reported 4 high, 10 medium and several low findings. All were accepted:

- **High:**
  - The build had no resolvable `node_modules` and would have written into the immutable source; it now builds in a temp copy with a symlinked, separately locked cache and `--base` (§8.3).
  - The every-dollar claim missed unattributable, deleted-bead and outside-project spend; ledger cards, deleted-bead cards, an "Other" group and a `--reconcile` oracle were added (§5.1).
  - Scheduling and budget did not scale to project sessions; scheduling is now activity-first, with a priority-ordered budget and read-time states (§8.6, §5.3).
  - Codex spawned threads would each have become a card; they are now folded into their root parent (§4.1).
- **Medium:**
  - Project resolution now uses the git common dir, and bootstrap settings and `LaunchContext` changes are specified (§4.1).
  - Beads reads and cache are now declared: `bead_rows`, `bd show` and comments, and a third query template (§7.1).
  - Request roles split warden subagent spend correctly (§4.2).
  - Breakdowns are aggregated from bead request rows with `share_picos` (§7.1).
  - Builds are keyed by tree, remember failures and have no code splitting (§8.3).
  - The resident module is limited to routing and static files, and builds run as a source-selected subprocess (§8.2).
  - `Sec-Fetch-Site`, additional headers, static-path hardening and `--q=` were added (§8.2).
  - A discovery-failure retention guard was added (§4.1).
  - Step 5 uses fixture builds, and the CI Node and network requirement is stated (§10).
  - Codex `exec` chunk parsing is specified (§4.3).
- **Low:** context-row assignment, lifetime card amounts, stable card keys, window definition, summary invalidation, Tollgate repository mapping and timestamps, the log-tail bound, the no-free-text rule for event rows, the excerpt-unavailable cases, and nits.

A second fresh review of revision 2 confirmed that the build and scheduling fixes work: a symlinked `node_modules` resolves under Vite, and `tg repo list` and `status --repository` behave as assumed. It reported 2 high and 4 medium findings. All were accepted:

- **High:**
  - Agent cards would have counted unattributable spend twice; they now carry `unowned_usd`, and the unattributable ledger is keyed by the bead's project (§5.1).
  - Folded Codex threads could have been collected under two keys, and the plan relied on children never claiming. Every Codex ID now resolves to its root, a child's own intervals take precedence, detection uses `source` rather than `thread_source`, and the cost-plan storage, identity, view and invariant amendments are listed (§4.1).
- **Medium:**
  - The budget now fits within 5 seconds, and orders 4–7 are stated to starve while cost-plan work catches up (§8.6).
  - Environment build failures retry with backoff (§8.3).
  - Client routes serve `index.html` (§8.2).
  - Activity uses all of a root's files, so delegation does not look stalled (§5.3).
