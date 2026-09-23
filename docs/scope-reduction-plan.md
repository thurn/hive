# Proposal: finish Hive by reducing its responsibilities

Date: 2026-09-23

Baseline: Hive commit `8f2b36022d0e682e37393ec4388869cc32a9280c`

Revision: native claiming and dependencies; bead-linked observation and archival; reduced validation scope.

Status: implementation-ready proposal; this document does not itself change running services or authorize production cutover.

## 1. Objective and authority

Revise the existing `/Users/dthurn/hive` repository into a small, usable system in which **agents run the workflow, Beads stores work, Tollgate delivers changes, and Hive supplies observation, source selection, and only demonstrated native integration gaps**.

Implement this proposal in the existing repository. Preserve prior implementations in Git history; delete obsolete code, commands, metadata readers, tests, and documentation from the active tree. Do not preserve compatibility with the superseded execution model. Moving that model into fewer files is not completion.

Once adopted, this document replaces the implementation requirements in `/Users/dthurn/fulcrum/hive-design.md` and the old scope assertions in Hive's README, AGENTS.md, invariants, skills, and implementation ledger. Update those Hive references in the first implementation commit. Do not edit Fulcrum's runtime or reinterpret its existing assignments. The old design remains historical context and supplies no additional acceptance requirements.

This proposal contains the complete scope and acceptance contract. The implementation must inspect the current code and installed native tools, but does not require earlier conversations to determine product decisions. If master has advanced beyond the baseline, reconcile intervening changes against this contract before deleting or retaining them.

The governing rule is: **if a skill plus an existing native command can perform the operation reliably enough for the contract below, Hive must not implement another subsystem for it.** Every retained or added custom command must identify a concrete native gap and the acceptance scenario demonstrating it.

## 2. Finished product and explicit exclusions

The finished release supports one host, several configured projects, and one explicitly selected server-mode Beads database. Independent executors file, claim, implement, review, deliver, close, rename, and continue work. **Preventing two different executors from owning the same bead is the important concurrency guarantee. Native Beads atomic claiming supplies it.**

Eight assigned unfinished beads is a workload guideline, not an enforced ceiling. Occasional overshoot is acceptable. One unfinished bead per executor is a skill convention, not another database invariant. Project scope, readiness, approvals, and resource pressure are checked by agents. There is no dispatcher, global admission service, or automatic worker revival.

The release includes:

- Native Beads creation, atomic claiming, dependency edits, queries, closure and explicit repair, with clear skill instructions and database routing.
- Eight role skills: executor, bead, warden, weaver, sage, vizier, justiciar, and a conservatively scoped manual archivist.
- Native Tollgate worktrees, submission, approval and blocking waits; a transport-only bridge only if actual Codex acceptance demonstrates the need.
- Incremental collection and API-equivalent costs **per Codex thread**, showing the beads associated with that thread. The worklist comes from creator and assignee thread IDs stored in Beads.
- Manual archival using that same deduplicated bead-linked thread list and native activity checks.
- Local-master source selection, consistent running-call imports/assets, and continuity of necessary resident connections.
- A small regression suite for Hive-owned behavior, one authoritative pre-promotion gate, and bounded initial native acceptance.
- Installation, explicit maintenance, manual backup/restore and safe cutover instructions. Production activation is a separate operator action.

Explicitly remove or exclude:

- `hive claim`, `hive dep`, `hive task` CRUD, `hive admission` and custom claim/readiness/next-work APIs. There are no compatibility aliases.
- The global admission lock, capacity counter or enforcement, durable uncertain-write marker/barrier, admission stop/reopen controls, dependency/claim transaction protocol and their recovery machinery.
- Session enrollment, `hive session enter`, title acknowledgements, desired-title storage, conversation lifecycle records, phase transitions and delivery orchestration.
- Automatic abandoned-owner recovery, writer inventories, turn transfers, lease expiry, automatic replay of uncertain writes and hook-driven pause/resumption.
- Stop-hook reminders, hook deployment/trust work, periodic worker revival, per-project ceilings and live resource scheduling.
- General Codex conversation discovery through titles, skill-invocation scanning or index enumeration. Unlinked conversations are outside default observation and archival coverage.
- Exact response-start attribution, per-bead/per-role cost allocation, project cost aggregation, complete traces and automatic model/tier inference without evidence.
- Scheduled archival, archive history, persistent unarchive exemptions, periodic GitHub backups and unattended restore. Manual operations remain included.
- General migration machinery, schema-version chains, custom hashes, database fallback and direct Dolt task-table access.
- Linux production support. Optional asynchronous Linux verification does not become another delivery gate.

These are product decisions, not implementation gaps to quietly reintroduce. Record withdrawn requirements as retired or deferred scope, not features completed by deleting their tests.

## 3. Evidence motivating the revision

At baseline `8f2b360`, Hive contains task, phase, session, delivery, hook, configuration and uncertain-write machinery. Native recovery, deployed hooks, automatic archival, backup and the assembled workflow remain incomplete. This proposal removes much of that unfinished scope rather than completing those subsystems.

The inspected native versions were Beads 1.2.2 (`6c124203e`) and Dolt 2.2.0. Relevant observed behavior:

1. `bd update <id> --claim` atomically sets assignee and `in_progress`, with idempotence for the same owner. Each executor must supply a different real Codex thread ID as actor; a shared default username defeats the distinction between executors.
2. A disposable probe successfully claimed a bead whose prerequisite was unfinished. Native atomic ownership does not enforce Hive's former readiness policy. Agents must check readiness themselves.
3. `bd create --deps` attaches edges after creating the issue. Do not treat a multi-flag native command as a crash-atomic filing transaction. `--claim --metadata` likewise includes separate native writes; creator metadata need not be part of claiming.
4. Native creation with a past `--defer` date produces an open issue. An indefinite hold can be established with native `status=deferred` and an empty deferral date. Use the filing convention below, not a far-future date as permanent approval protection.
5. `bd close` retains assignee. A cancellation close reason still causes native readiness to regard the prerequisite as closed. Agents inspect the recorded outcome before proceeding.
6. Explicit database routing matters: another cwd or conflicting environment must not silently select a different store.
7. Tollgate can complete remote synchronization while a dirty main checkout leaves local master behind. Its native delivery interface must expose the configured completion outcome clearly.

Use these findings to implement skills and thin native boundaries; do not create a generic native-tool verification framework. Pin supported test versions and check relevant changed assumptions in the small initial workflow.

The baseline suite ran locally, again in Tollgate, and again on GitHub macOS and Linux. For `8f2b360`, Tollgate approval/wait took about 200 seconds, then hosted completion another four minutes. Hosted check steps took 210 seconds on macOS and 187 seconds on Linux. There were 83 tests and roughly 27 disposable-server fixture sites. The revised delivery policy eliminates repeated blocking stages and tests for removed guarantees.

## 4. Authority, data and native task operations

### 4.1 Authority and settings

| Information or decision | Authority |
| --- | --- |
| Task text, status, priority, assignee, edges, outcome and thread references | Native Beads records |
| Exclusive ownership of one bead | Native Beads atomic claim with a unique executor actor |
| Readiness, project scope, approvals and approximate workload | Executor skill and user instructions |
| Worktree, candidate, validation, promotion and synchronization | Tollgate |
| Native identity, titles and activity | Codex |
| Per-thread usage, estimates and associated-bead list | Derived telemetry |
| Source, state, Beads routing and configured project paths | Local Hive configuration |

Keep `~/.config/hive/bootstrap.json` and defaults `~/hive`, `~/.local/state/hive`, and `~/brain/hive` for repository, state and Beads directory. Preserve `HIVE_BOOTSTRAP_CONFIG` for isolated environments. Store necessary project IDs, absolute repository/invariant paths and optional native project IDs in that local configuration. Delete Beads infrastructure-record registration and capacity-edit protocols. Do not add a `global_limit` setting or live capacity-control API; the eight-work-item guideline lives in skills.

Change routing/project bindings during explicit maintenance with participating writers stopped. Configuration validation remains small and typed. Conversation identity requires neither enrollment nor a native turn ID.

### 4.2 Native fields and thread references

Use native status, assignee, priority, descriptions, dependencies, comments/notes and ordinary metadata. Do not persist a parallel lifecycle or phase.

Use flat metadata keys to preserve unrelated native metadata:

- `hive_project`: the intended configured project for skill selection and scope inspection.
- `hive_origin_thread`: the actual Codex thread ID that filed the bead. Include it in the native creation metadata when available; if unavailable, show missing association rather than inventing an ID or requiring enrollment.
- `hive_resolution`: `completed` or `cancelled`, written with the close outcome for straightforward agent inspection. This is an outcome annotation, not a Hive-enforced dependency predicate.

The assignee is the actual executor's Codex thread ID, set by native claiming. Retain it after closure as a useful historical link. Creation and execution can occur in different threads. References to worktree, source commit, candidate, artifact, design or other conversations may remain in ordinary notes; no mandatory phase schema or review-thread registry is added.

An assigned unfinished bead indicates work still in progress, including review, CI or unresolved interruption. Closed-bead assignees are historical and should be excluded from an agent's rough workload count. Unknown active work is a reason for caution, not a reason to build a global admission validator.

### 4.3 Native interface and routing

Skills use native `bd` verbs, including claims and dependency changes. Always name the bead explicitly; never rely on the last touched issue. Use explicit atomic `update --claim` rather than plain assignee overwrites or native auto-claim-next options that skip the skill's readiness/identity checks.

Keep the explicit server-routing validation and environment sanitization. The proposed `bin/hive-bd` is a routing-only launcher through the existing source selector: it fixes the configured Beads directory/endpoint/database, disables fallback/auto-start and remote synchronization on ordinary operations, and forwards native arguments/stdin/output/exit status. It has no task policy, locks, retries, admission model or new request/response grammar. Direct `bd` with the same validated routing environment is equivalent.

Reject native routing overrides such as `--global`, `--db`, `-C`/`--directory` and `--repo`, including equivalent flag forms, when they would override the chosen store. A description or stdin string containing those words is not an override. Test this narrow routing boundary. Do not turn the launcher into another task CLI.

### 4.4 Filing, dependencies and closure

- **Ordinary ready work:** create with meaningful description, acceptance criteria, project, priority and creator thread ID.
- **Prerequisites or missing approval:** initially omit `hive_project`, but record intended project/prerequisites in the description and creator thread ID in metadata. Attach native edges. For an approval hold, set native deferred status without an expiring date. After checking the intended edges/hold, add the project metadata. Skills do not select unfinished filing with missing project metadata, including when given its ID directly. This is a cooperative convention; native claiming does not enforce it.
- **Dependency edits:** use native `bd dep add` and its native removal command (`bd dep remove`/`rm`, as supported by the selected version). Beads owns edge storage and cycle detection. Establish prerequisites before offering work. Do not change assigned work underneath its executor; coordinate with its owner. If your own work needs a new prerequisite, checkpoint and defer it, stop/inspect outstanding effects, then attach the dependency. Release your assignment only after work is settled.
- **Readiness:** inspect native status, dependency records and recorded outcomes. Open, deferred, cancelled or ambiguous prerequisites mean wait or resolve the relationship. Native `bd ready` is a useful candidate list, not proof that cancelled work accomplished a prerequisite. No Hive code enforces this policy transactionally with claim.
- **Successful closure:** finish delivery/checklist and settle writers, record `hive_resolution=completed` with a concise outcome, then close natively. On uncertain output, inspect the bead before continuing. Retain assignee for log/cost and archival association.
- **Cancellation:** record `hive_resolution=cancelled`, close explicitly, and inspect affected dependents. Native readiness may expose them; agents must not mistake cancellation for successful completion. Revise their prerequisites or cancel them deliberately.
- **Reopening/repair:** coordinate affected workers before invalidating a completed prerequisite. Clear historical assignee/resolution before offering reopened work; a new executor then claims atomically. Never clear another active owner's assignment merely because it looks old.
- **Pause:** an explicit user pause remains until explicit resumption; design approval and other unresolved conditions remain separate instructions in the bead. A new message is not implicit permission to restart paused implementation. Retain assignment while writers or external outcomes remain unknown.

Readiness inspection, edge edits and claiming are not one transaction. That race is accepted in this design. Do not recreate a locked dependency helper, filing journal, hidden publish command or global stop to eliminate it.

## 5. Native claiming and manual repair

### 5.1 Claim exactly once as the real executor

After inspecting scope, status, prerequisites and rough load, invoke the native atomic operation with the current conversation's actual identity:

```sh
bd --actor <native-codex-thread-id> update <bead-id> --claim --json
```

The example assumes explicitly configured routing; the routing-only `hive-bd` launcher accepts the same native arguments. Do not use the default OS/Git username, a shared `executor` label, a random replacement identity, a previous owner's identity or a native turn ID. Two separately recruited conversations use distinct thread IDs. If the invoking thread ID cannot be established, stay read-only/file what can be filed and report the missing identity rather than claiming as a shared actor.

Proceed with implementation only after the native claim succeeds. If another owner won, do not overwrite it; choose another eligible bead or report the requested bead is unavailable. Repeating a claim as the same owner may be idempotent; it is not proof that the owner's earlier tools stopped. On later entry to the same conversation, inspect its assignment, user instructions and outstanding commands before continuing.

Aim for one assigned unfinished bead per executor and about eight across the host. Inspect native work and actual resource pressure when useful. Concurrent executors may exceed that guideline. No claim lock, capacity counter, global marker, slot release command or cross-bead invariant is required.

Native claims protect ownership from competing actors; they do not prevent a noncooperating agent from editing without a claim, overriding assignee directly, or ignoring a pause. State this boundary accurately in skills and acceptance evidence.

### 5.2 Lost replies and repair

A lost native claim response is ambiguous. Inspect the same bead and its assignee before retrying or selecting another. If it is assigned to the invoking thread, reconcile the existing work. If it is assigned elsewhere, do not edit. An apparently unchanged record alone does not prove an old request cannot later complete; retain uncertainty and resolve the outstanding client/provider operation before proceeding with that attempt.

This uncertainty affects the involved work. It does not create a Hive-wide write barrier. Other executors may continue claiming other beads through Beads' own atomic ownership operation. Do not add automatic replay or owner takeover.

Justiciar is a skill/runbook: stop relevant damage, identify affected native operations and writers, inspect workspace/candidate/Beads state, preserve explicit pauses, and repair using native tools. Ownership is reassigned only after the previous writer and external work are settled. If that cannot be established, leave the affected assignment for investigation. Controlled native server maintenance may be necessary in an exceptional incident; coordinate shared users and document the action rather than building a process inventory or recovery service.

Delete `hive admission stop|inspect|reopen`, the admission lock and `write_barrier.py` along with their callers. Source-snapshot preparation and telemetry still retain their own necessary local locking/transaction behavior; removing admission machinery does not mean deleting all locks indiscriminately.

## 6. Skills and the executor workflow

Role entry identifies the configured project and actual Codex thread, reads project instructions/invariants and relevant native work, then renames directly through Codex. There is no `hive session enter`, session record, title acknowledgement, native turn enrollment or phase command. Naming failure is visible and can be retried at a meaningful transition; it does not block delivery.

Role emojis remain executor ⚒️, bead 📿, warden 🛡️, weaver 🧵, sage 📖, vizier 🔮, justiciar 🔥 and archivist 📁. Titles are useful UI only. Observation and archival obtain thread IDs from beads, not title matching.

The ordinary loop is:

1. Clarify scope, search for duplicates, and file native beads with project, priority, acceptance criteria, prerequisites and creator thread ID. Keep unapproved work deferred; reuse an explicitly supplied bead.
2. Inspect readiness, existing assignments, likely conflicts and resource pressure. Stay within the starting project. Eight concurrent unfinished assignments is guidance; unknown metrics are not evidence of spare resources.
3. Claim through native `bd --actor <thread-id> update <bead-id> --claim`. Only an acknowledged claim authorizes implementation. A failed explicitly requested initial claim does not authorize unrelated work without appropriate scope.
4. Create an isolated native Tollgate worktree. Preserve useful references in the bead's ordinary notes without phase transitions.
5. Implement, run fast/relevant checks and commit. Obtain a fresh warden subagent with no inherited conversation, providing scope, diff/base/source, workspace, invariants and relevant checks. Fix substantive findings or explain disagreements; review source-changing fixes. A reviewer is part of this work, not a separately claimed implementation assignment.
6. Submit/approve natively and block for Tollgate's result. Handle in-scope failures/conflicts within the same bead. Keep the assignment during review, CI and draining or ambiguous external work.
7. Confirm delivered behavior and configured synchronization, settle writers, file useful follow-ups, record outcome/references and close natively. No Hive release or completion command is involved.
8. Rename meaningfully, inspect the next eligible bead in the same project and repeat until paused, blocked, out of scope or workload judgment says stop. No stop hook or scheduler forces continuation.

A returning executor inspects the latest instructions and retained workspace/candidate before editing or repeating submission. Already-promoted work needs completion, not reimplementation. Unknown activity is resolved manually; a peer does not adopt another thread's assignment during ordinary work.

Recruitment, where authorized, uses native creation of independent executor conversations with explicit project/scope. Each uses its own thread ID for native claims. There is no slot reservation, recruitment registry or parent-managed lifecycle.

| Role | Required behavior |
| --- | --- |
| Bead | File through native Beads with creator ID; preserve the enclosing role when invoked inline; do not start implementation merely because work was filed. |
| Warden | Review scope, behavior, architecture, invariants and test quality; obtaining fresh review is mandatory for implementation, while findings are advisory. |
| Weaver | Write a standalone design and obtain fresh cold document review and user approval before implementation. Closing a draft-authoring bead does not approve the design. |
| Sage | Investigate workflow/tool problems with evidence, file bounded improvements, and enter executor when implementation is authorized. |
| Vizier | Investigate initially read-only; enter executor normally before authorized implementation. |
| Justiciar | Stop damage and repair explicitly without delegation; preserve pauses and record emergency bypasses; obtain ordinary validation when possible. |
| Archivist | Perform manually requested cleanup of bead-linked conversations, using the scope and activity rules below. |

Read-only investigation and filing need no claim. A substantial external artifact can use a native bead, claim, fresh review and recorded acceptance outcome without a separate artifact lifecycle. Repository documents use Tollgate like other project changes.

## 7. Native Tollgate boundary and blocking waits

Skills invoke native worktree creation, candidate submission, approval and waiting. Existing native forms are `tg worktree create NAME --json`, `tg candidate HEAD --json`, `tg approve ID --wait --json`, `tg wait ID --json`, and `tg status ID --json`; confirm the selected provider's actual contract in a disposable repository. Run waits from a durable project directory because promotion may remove a clean source worktree. Stop workspace-rooted background writers first.

Delete Hive's delivery policy/model, source/candidate reconciliation, settlement transitions, and local synchronization interpretation. Tollgate must own the meaning of delivery finished.

Before deleting the last delivery check, use Tollgate's documented interface and focused provider-side evidence to establish that native completion covers:

- The exact selected repository and candidate, submitted source and actual tested integration result.
- Promotion and both configured local and remote synchronization, including explicit disabled policies.
- Distinct actionable failed-CI, conflict, cancelled, timeout/disconnected, blocked-sync and unknown outcomes.
- Pending/draining provider work where a cancellation acknowledgement does not mean all work stopped.

If current Tollgate output lacks these distinctions, implement the smallest native Tollgate interface correction in `/Users/dthurn/tollgate`, following that repository's instructions and tests. This is a bounded prerequisite to removing Hive's adapter, not permission to redesign Tollgate. Until it lands, skills explicitly inspect the native candidate and actual local-master inclusion; the ledger marks the native boundary unfinished. A temporary skill check is not the final completion criterion.

Try the actual Codex tool host with native blocking waits first. If it cannot keep a native wait pending without model polling, retain the existing MCP connection transport but change it to forward the native candidate-specific wait and native results. It may validate transport arguments, manage one wait-client process group per request, bound output/deadlines, and drain clients on cancellation/EOF. It may not inspect Beads, release ownership, authorize delivery, reconstruct synchronization policy, or manage execution phases. Cancellation stops observation, not provider work.

Accept a one-hour default native wait with adequate host timeout. Record one initial real wait lasting at least 30 minutes with no repeated model-driven polling. If a custom bridge is retained, test its interruption handling in a separate short scenario. Do not run the long wait in ordinary commit CI or repeat it without a relevant host/transport change.

## 8. Observation from thread IDs stored in Beads

### 8.1 One small candidate list

The observer reads native bead records, **including completed beads**, and extracts `hive_origin_thread` plus assignee values that are valid native Codex thread references. Deduplicate thread IDs and retain their associated bead IDs and relationship (creator or executor). Validate references at the native lookup boundary; legacy/non-Codex assignees and missing IDs are visible gaps, not guessed identities.

Replace session enrollment with this derived worklist. Do not enumerate the entire Codex conversation index, parse titles for membership, scan skill invocations, or require worker entry/exit commands. The only new filing convention is storing the creator ID in the native create metadata; claiming already supplies the executor ID. Preserve useful closed-bead associations. If explicit repair replaces an assignee, record the old thread reference in the repair note for investigation; parsing free-text history to recover every historical worker is outside this release.

Look up transcripts for these known IDs using a supported native API or the existing narrow read-only native index adapter. The index is a path lookup, not a second discovery source. Bound lookup and ingestion, validate observed schema and transcript identity, and fail visibly on mismatch. Never write Codex storage or build guessed schema-version fallbacks.

Retain a derived cached worklist and existing cursors so collection can continue from previously known IDs during a Beads outage, with stale discovery reported. That cache is disposable observer state, not a live registration requirement. Use supported native reads for refreshing bead references; if they require a complete bead listing, keep it off the task path and report its cost rather than inventing unsupported pagination.

### 8.2 Per-thread costs and honest coverage

Retain bounded incremental transcript ingestion, response-ID deduplication, cursor/usage atomicity, model observations, retained rate evidence, exact arithmetic and explicit missing/unpriced usage. Telemetry never authorizes or blocks task operations.

Required output is `hive cost --task <native-thread-id>` with observed thread totals, pricing assumptions, coverage/freshness and its associated beads, plus `hive telemetry status` for discovery/collection health. Existing explicit per-thread collection remains usable for an operator-supplied ID without making that thread a default archival candidate.

A creator thread may file work executed elsewhere, and an executor thread may serve several beads. Report the thread's cost once and list the associations. **Do not allocate its full cost independently to each bead.** Any combined total deduplicates native response IDs across the selected threads. Bead/project/role cost allocations are not required. A native thread link establishes association, not a justified token split or complete project accounting.

Unlinked conversations, unrecorded review subagents and read-only specialists may be absent. State that limitation. A linked thread can also contain unrelated work; its total remains a thread total. Do not add reviewer bookkeeping or broad discovery solely to make the report appear complete. Unknown upstream model/tier remains an explicit assumption or unpriced usage, never a silent substitution or zero.

### 8.3 Collection and retention

Keep bounded `telemetry sweep/watch` (or the existing equivalent). Each batch uses fresh local-master code; the resident retains only timer/connection continuity. Collector outage, missing transcripts or corrupt telemetry must not prevent native filing, claiming or delivery.

Provide opt-in host-service and install/uninstall instructions, validated with a disposable configuration. Do not activate a production service during implementation. Source transcripts remain under Codex's control. Derived telemetry is retained until explicit reset; report its size and document reset while the collector is stopped. No automatic retention scheduler or per-bead compaction is needed. A full reset intentionally discards retained historical price evidence.

## 9. Archival, backup, maintenance and cutover

### 9.1 Manual archivist uses the same bead-linked IDs

Archivist obtains candidate IDs from the same creator/assignee references in Beads described in section 8, including closed beads, and deduplicates them. It need not run cost collection or depend on the telemetry database to do this. No enrollment registry, title-based discovery or complete conversation inventory is introduced. An unlinked thread is outside the default candidate list, though the user can explicitly supply it.

Act within the user's requested scope. An invocation authorizing cleanup of inactive bead-linked Hive conversations supplies that scope; do not request redundant confirmation for each eligible conversation. A broad discovery result alone is not authorization to archive unrelated conversations.

For each candidate, require authoritative native evidence of no active/queued turn and at least 15 minutes without input, output or tool activity. A pending tool call remains active. Recheck immediately before acting and inspect descendants affected by the native archive operation. Unknown activity or descendant effects means skip. A closed associated bead is never proof of inactivity: the same thread may be working on another bead.

Prefer native conditional archival. Otherwise recheck afterward and promptly unarchive conversations/affected descendants if activity raced the request. This is eventual UI repair, not atomic exclusion. Never stop a turn to make it eligible; archival changes no bead ownership. If tools cannot establish safe inactivity, report that limitation and skip instead of building a conversation lifecycle subsystem.

There is no scheduled archival, history database or persistent unarchive exemption state. A manually unarchived thread is not revisited by an automatic sweep; any later archival requires a new explicit user scope. Missing bead links reduce cleanup coverage and are acceptable.

### 9.2 Manual backup and GitHub publication

Deliver a native backup/restore runbook for task intent. Exercise one disposable round trip preserving descriptions, priorities, edges, metadata (including creator IDs), notes/comments, outcomes and historical assignees.

Use supported Beads export/backup operations and record their verified exact commands. Prefer a consistent native export; if concurrent-write consistency is unavailable, stop participating writers for the manual snapshot. Do not copy live database files or add raw Dolt task-table code. There is no Hive admission stop to invoke; writers are quiesced operationally.

For manual GitHub backup, write the export to an explicit dedicated Git repository, commit and push only if contents changed, and preserve the local snapshot if publication fails. Do not inherit an unrelated parent remote. This is one-way backup of task records, not automatic synchronization/import into live assignments. Periodic GitHub publication, retry daemons and unattended restore remain deferred; they are not added by this revision.

Restore into a new isolated store with executors stopped. Compare task intent, then inspect historical assignments and candidates before resuming. Never overwrite a live store as an acceptance test or infer that a restored assignee is a running owner.

### 9.3 Exceptional maintenance and live updates

Preserve source selection: new Hive invocations use committed local master; existing calls retain their selected imports/assets; broken preparation fails visibly; ordinary code updates require no reinstall/restart. Preserve necessary resident connections and pending requests.

Remove admission-state maintenance machinery with the custom task model. Retain source preparation and telemetry locks/transactions where they protect actual remaining behavior. Dependency/environment or incompatible metadata changes use explicit stopped maintenance: quiesce participating native writers and relevant calls, back up, change, validate and resume. No migration chain, fallback or in-process source mixing is added.

### 9.4 Installation and cutover

Verify whether production Hive state now exists; the baseline ledger said it did not. Preserve Fulcrum's database, assignments, services, skill links and configuration. Use isolated state and source-qualified Hive skill paths throughout acceptance.

For cutover, refile selected intent with fresh native IDs and original-record links. Do not import old ownership/phase/session state or make replacement work available while Fulcrum still executes it. Deliver tested setup, rollback, collector and optional MCP configuration instructions. Rebinding live skills, activating services, initializing production state and moving actual work are separate operator-approved actions, not silent side effects of implementing this plan.

### 9.5 Simple skill installation

Implement `scripts/install-skills` as a small installation script, with no package manager or resident installer. It creates symlinks for the eight role directories and their `shared` instructions, preserving relative links between them. Default the source repository to `~/hive` and destination to `${CODEX_HOME:-$HOME/.codex}/skills`; support `--source <repository>` and `--dest <directory>` for other installations and disposable checks. Example:

```sh
~/hive/scripts/install-skills
```

Link to the stable Hive checkout, never a temporary implementation worktree. Existing links to the same targets are a successful no-op. Preflight all destinations and refuse conflicts with a clear message before changing anything; do not overwrite Fulcrum skills or unrelated files. The operator resolves old skill-name conflicts during the explicit cutover, then reruns the script. Print the installed locations and concise usage, such as invoking `$executor` or `$bead`. Source-linked skill edits require no reinstall; do not restart services to activate them.

Keep setup limited to these links: no Beads initialization, hooks, service activation or migration. Document uninstall as removing only the links created by this script, leaving source and unrelated skills intact. Verify installation, repeat invocation, relative shared-instruction links and conflict refusal in one disposable-directory check. Add the setup command and prerequisite Beads/Tollgate configuration links to README; no installer framework or separate manual-QA matrix is needed.

## 10. Code disposition

Baseline file names identify removal targets, not mandatory replacement modules. Preserve history in Git and keep only useful remaining code.

| Existing area | Required disposition |
| --- | --- |
| `session.py`, `session_store.py`, session commands/tests | Delete; creator IDs and native assignees provide observation references, and Codex handles naming. |
| `model.py`, `transitions.py`, `phase_json.py`, `state_json.py`, lifecycle/ownership protocol tests | Delete custom execution state, phases and transition serialization. Retain minimal immutable data only where remaining native reads need it. |
| `admission.py`, `task_service.py`, `filing.py`, claim/next/dep/task CRUD APIs | Delete. Native Beads commands plus skills replace the entire custom coordination layer. |
| `write_barrier.py`, admission-lock and stop/reopen code/tests | Delete. No global uncertain-write marker or manual-clear command remains. Preserve unrelated source-cache and telemetry locks. |
| `configuration.py`, `configuration_store.py`, infrastructure records | Replace with small local routing/project settings; remove global/per-project capacity enforcement and registry mutation APIs. |
| `bead_json.py`, `beads_store.py`, `task_status.py` | Delete lifecycle codecs and task-service plumbing. Extract only minimal native bead-reference reads needed by observation/archivist. |
| `delivery_commands.py`, `delivery_settlement.py`, `tollgate.py`, `tollgate_model.py`, `tollgate_process.py`, `local_sync.py` | Delete Hive delivery policy when the native Tollgate completion contract is usable; fix a demonstrated provider gap in Tollgate. |
| `hooks.py`, `hook_events.py`, `reminders.py`, hook tests/configuration | Delete. Do not deploy replacement hooks. |
| `beads_connection.py`, `beads_process.py` | Retain only routing/environment validation and narrow native reads/transport actually needed. No task mutation policy or Hive claim/dependency protocol. |
| `collection*.py`, `native_transcripts.py` | Use deduplicated IDs from bead creator/assignee fields, including completed work. Remove enrollment and broad native-index discovery. Keep bounded path lookup, ingestion and resident continuity. |
| `usage.py`, `usage_store.py`, `transcript_chunks.py`, `pricing.py`, `cost_report.py`, `turn_model.py` | Preserve incremental accounting and native model observations. Add associated-bead lists to thread reports; do not add bead/project/role allocation machinery. |
| `mcp_transport.py`, `mcp_wait.py` | Delete if native Codex waiting suffices; otherwise retain transport only, without Beads or delivery policy. |
| `src/hive_bootstrap`, launcher and source tests | Preserve local-master selection, immutable running-call source and useful isolation; remove obsolete admission-maintenance coupling. |
| `commands.py`, `cli_parser.py`, `command_handler.py`, `cli_display.py` | Remove deleted command branches and request/response models. Public Hive commands cover source diagnostics, telemetry/cost and optional native wait transport only. |
| README, AGENTS, skills, docs and ledger | Rewrite for this contract; remove old hard-capacity, phase/session, automatic-recovery and serial-hosted-CI requirements. |

Keep application data immutable and typed, with validated external boundaries and no `Any`, unchecked casts or broad suppressions. Do not preserve a deleted abstraction merely because its tests or types are extensive.

## 11. Reduced CI and development feedback

### 11.1 One authoritative promotion gate

Use isolated Tollgate worktrees, Conventional Commits and fresh cold review of implementation diffs. Reviewers receive scope/diff/checks without the author's conversation. Do not delegate implementation unless requested.

- `scripts/check-fast`: lint, formatting, strict typing, boundary rules and fast tests for retained Hive behavior.
- `scripts/check`: fast checks plus the small retained integration suite. Tollgate runs it once against the actual integration candidate before promotion.
- During implementation, run fast checks and relevant tests. Do not require a duplicate full local suite immediately before Tollgate repeats it. Run broader tests only for a concrete unresolved concern.
- Run `scripts/prepare-check` when preparing an environment or after dependency/lock changes. Reuse unchanged validated dependencies where possible; retain correct fresh-environment preparation in Tollgate.
- Remove the duplicate GitHub macOS full-suite run. Any retained Ubuntu clean-environment/portability job runs asynchronously after promotion; executors do not wait before continuing. Report actual failures and fix them deliberately, rather than repeatedly rerunning unchanged code until green.

Update AGENTS, documentation and handoff instructions accordingly. A local pass does not replace Tollgate's test of the actual integration commit, which can differ from submitted source. Do not claim asynchronous hosted checks have passed while they are pending.

### 11.2 Test what Hive still owns

| Retained boundary | Small regression coverage |
| --- | --- |
| Native routing launcher | Configured store survives unrelated cwd/conflicting environment; explicit routing overrides are rejected; outage does not fall back or auto-start another store. |
| Source selection | New calls see committed master; existing calls retain imports/assets; failed preparation is visible; concurrent snapshot preparation remains safe. |
| Bead-linked observation | Creator and assignee IDs from open/closed beads are deduplicated; bad/missing references remain gaps; several linked beads do not multiply a thread total. |
| Incremental usage/cost | Bounded progress, atomic cursor/usage updates, response deduplication, exact pricing with retained assumptions/evidence, visible gaps and recovery after collector restart. |
| Optional wait transport | Pending connection remains responsive; cancellation/EOF drains only observation clients; later calls use fresh source. No Beads fixture is needed for transport tests. |

Delete tests of sessions, phases, hooks, title acknowledgements, Hive admission/capacity, dependency/claim races, uncertain-write barriers and automated repair alongside the code. Beads owns atomic claims and cycle detection: demonstrate correct actor wiring and native use once in assembled acceptance, rather than rebuild an upstream concurrency suite. Tollgate owns delivery semantics and associated regression tests; keep only relevant Hive transport coverage and the native workflow smoke.

Remove Beads servers and Git repositories from tests that only read derived telemetry or test pure accounting. If a retained native-boundary test needs a server, reuse setup with isolated databases where safe. Keep dedicated servers only for a real remaining outage/process boundary. Use deterministic signals, bounded waits and useful failure output. Profile before adding parallel execution or fixture abstractions.

Reference-host targets: warm fast checks at most 30 seconds, full gate at most 120 seconds. Record three consecutive quiescent-host runs once after suite reduction, with cold provisioning reported separately. These are proposed budgets, not measured achievements. If missed, identify the concrete bottleneck; do not silently weaken assertions or start an open-ended optimization project. A budget change must be explicit.

No real 30-minute wait, broad load matrix, manual recovery exercise or backup/restore run belongs in per-commit CI. Reuse recorded initial acceptance until the relevant integration boundary changes.

## 12. Implementation sequence and exit conditions

Use independently passing commits; combine deletion and replacement where separation would leave broken imports or misleading instructions. A release acceptance scenario is not rerun for every commit.

| Order | Work | Exit condition |
| --- | --- | --- |
| 1 | Adopt scope and shorten feedback | README/AGENTS/invariants/ledger reference this revision; fast/full checks separated; hosted completion no longer blocks continuing work. |
| 2 | Remove custom coordination and rewrite native skills | Native claims use real distinct thread actors; native dependency/filing/closure recipes are documented; Hive claim/dep/admission/barrier/session/phase/hook machinery and corresponding tests are deleted coherently. Minimal routing remains. |
| 3 | Replace enrollment with bead references | Observer and archivist derive IDs from creator metadata and native assignees including completed beads. Thread cost reports list associated beads without allocating costs per bead. No index enumeration or title discovery is required. |
| 4 | Complete native delivery and prove the short workflow early | Required bounded Tollgate interface correction, if any, lands; optional bridge contains transport only; a real executor delivers two dependent beads with review, closure, direct naming and follow-on native claim. |
| 5 | Finish small operational deliverables | The simple skill installer works; manual archivist, native repair guidance, one backup/restore round trip, explicit maintenance, opt-in collector setup and Fulcrum-safe cutover instructions are truthful and usable. |
| 6 | Record initial boundary evidence and finish the audit | The small acceptance set below passes, including one native long wait; retained tests meet or explicitly revise budgets; deleted protocols are absent from code/docs/test obligations. |

Adapt collector references in the same coherent change series as session removal. Do not spend time polishing obsolete abstractions before the first real native workflow. New custom commands/subsystems require a concrete failed native workflow and an explicit scope change.

## 13. Small initial acceptance set

Use one disposable registered Tollgate project/remote, an isolated Beads database and Hive configuration/state, and real Codex conversations. Never use production Hive/Fulcrum work as a fixture. Save concise evidence in `docs/acceptance.md`: tested commits/tool versions, commands, outcomes, elapsed times and known limitations. Do not commit private transcripts or credentials.

Initial acceptance is limited to these scenarios:

1. **One short executor journey:** file two dependent beads with creator IDs; inspect readiness; claim as the actual thread; use a native worktree; implement a small change; obtain cold review; deliver through Tollgate; record outcome and close; rename directly; then claim/deliver the second bead. Verify retained creator/assignee references. In this same disposable setup, make two distinct thread actors attempt the same bead and confirm one owner succeeds and the loser does not edit. This verifies use of the native primitive, not a new capacity guarantee.
2. **One real observation check:** collect the journey's linked threads incrementally, compare response totals against a read-only scan, and verify visible pricing assumptions/gaps. A thread serving both beads has one cost total with both associations. Stop/restart collection and verify it resumes without duplicating usage; native task work remains independent. Reuse automated ingestion edge-case tests instead of manually enumerating them again.
3. **One long-wait/source-update check:** a real Codex-to-native-Tollgate CI wait lasts at least 30 minutes without repeated model polling. While it is pending, advance committed Hive master in the isolated source repo; a new Hive command uses the update, existing Hive calls/assets remain consistent, and the native wait/required connection survives to completion. With a direct native wait, source consistency is checked on a separate retained Hive operation; do not invent Hive supervision solely for the test. Exercise cancellation in a separate short wait only if a custom bridge is retained. The long wait is one-time initial integration evidence, repeated only when its host/transport boundary materially changes.
4. **One manual operations check:** use the same bead-linked candidate list to verify archivist skips active/unknown threads and does not infer inactivity from closed beads. Archive a known eligible disposable thread only if the native observations support it; otherwise record the safe capability limitation. Perform one native backup/restore round trip in a new store. Review repair/cutover instructions for usable native commands and explicit stop/inspection behavior; do not build an exhaustive emergency simulation matrix.

Review the remaining skills for their short contracts and correct native commands. Separate real recruitment, every-role end-to-end journeys, phase permutations, hook events, owner-transfer protocols, archival-history races and global capacity/uncertainty exercises are not release blockers. Fresh review in the executor journey exercises the necessary delegation boundary; unlinked reviewer cost coverage may remain missing.

Known native delivery distinctions (failed CI, dirty local master, cancellation/draining and repository/candidate identity) belong to Tollgate's own focused tests and documented interface. If that interface is changed, test the affected case there and record the evidence. Do not duplicate the entire provider failure matrix in Hive manual QA.

## 14. Proportionate measurement and completion

Retire the unconditional sub-100ms p95 target, the 1,000-bead/eight-client admission benchmark, the 100-sample-per-operation matrix and all claim-lock/marker timing requirements. There is no Hive admission path to optimize. Do not respond to native command latency by adding direct database queries or a resident task service.

Record the fast/full suite timings in section 11 and the actual short workflow's wall time, model/tool exchanges, native command count, source startup and observed thread-level API-equivalent cost with coverage. Separate CI and model waiting from local command overhead. Use an existing comparable native-only workflow if available; no additional matched trial or statistically complete performance study blocks this release. If an operation is conspicuously slow, profile that operation and record a bounded fix or limitation. Do not invent per-bead cost allocation to report these measurements.

Completion requires:

- The active instructions and command surface match this document: no Hive claim, dep, session, phase, admission/barrier or duplicate delivery policy remains.
- Native claims consistently use the real unique thread actor, and the small competing-actor check demonstrates correct integration.
- Agent-owned readiness/approval/resource rules are accurately described as workflow behavior; eight is guidance and occasional overshoot is accepted.
- Observation and archival default candidates come from stored bead creator/assignee IDs, including completed beads. Per-thread costs show associated beads and never duplicate a thread total per bead. Missing links and incomplete coverage are visible.
- Source consistency, retained telemetry behavior and any custom transport have focused regression evidence.
- The short real workflow, observation check, one native long-wait/update check and small manual operations check are recorded, with safe archival capability gaps labeled honestly.
- The simple skill installer passes its disposable check, operational/install instructions are usable and Fulcrum is preserved. Production activation remains separate.
- One authoritative Tollgate gate exists; suite-budget evidence is recorded or an explicit revision accepted; optional hosted results are reported asynchronously.
- Applicable implementation commits passed checks, cold review and Tollgate. The ledger maps this bounded scope to evidence, concrete blockers or explicit exclusions; it does not revive superseded requirements.

## 15. Disposition of the former missing-work list

| Former area | Revised obligation |
| --- | --- |
| Native recovery | Manual inspection/native repair guidance; Hive writer inventory, barrier clearance, admission controls, automatic takeover and typed reentry are removed. |
| Codex integration | Real thread identity for native claims, direct naming and ordinary skill behavior; no hooks, trust setup, enrollment or required per-role integration matrix. |
| Archivist | Manually scoped candidates from bead-stored thread IDs, native activity checks and conservative skips; no schedule/history/exemption subsystem. |
| Backup/cutover | One manual native backup/restore exercise and safe setup/Fulcrum preservation; GitHub publication is manual, periodic syncing deferred. |
| Maintenance | Explicit stopped maintenance for actual dependencies/state, preserving source consistency; no admission-state conversion framework. |
| Observation | Incremental per-thread usage/cost from creator/assignee links; no broad conversation discovery or bead/project/role allocation requirement. |
| Assembled acceptance | One short real journey and one initial 30-minute native wait/update check, reused until relevant boundaries change. |
| Performance/final audit | Small suite budgets and observed workflow overhead; no hard capacity benchmark, unproven 100ms gate or broad load study. |

Finish this smaller workflow and its explicit operating boundaries. Additional automation or stronger guarantees require demonstrated need and a separate scope decision.
