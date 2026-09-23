# Deliver an admitted work item

This is the project-file delivery path shared by executor and weaver. It does
not independently authorize the scope, approve a design, or start another bead.
Read retained state first: resumed work may already have a workspace or candidate.

Use `hive workspace create <bead> --project <project> --owner <task> --turn
<turn> --json` only for a newly preparing bead. Record the returned workspace
with `task advance --phase-json` as `implementing` before editing. Work only in
that Tollgate-created workspace. Read project instructions and invariants there.
An existing workspace or lost acknowledgment requires inspection, not another
blind create.

Implement the agreed behavior and run proportionate checks. Commit source before
review. Record the reviewing phase with the workspace and exact native Git
commit ID; it is a resource identity, not a new Hive proof:

```json
{"kind":"reviewing","workspace":"/work/search-fg3","source":"<git-commit>"}
```

Run a fresh warden subagent with `fork_turns: "none"`. Give it the
[Hive warden skill](../warden/SKILL.md), bead and agreed scope,
repository/workspace, review base and exact source,
project invariants, and relevant checks. It must not inherit the author's
conversation. Record the parent title as warden while it runs. Review retains
the bead's slot; do not recruit an independently admitted executor for it.

Address valid findings and explain disagreements concretely. File worthwhile
out-of-scope findings. Source-changing repairs return the phase to implementing,
are committed, and receive fresh review of their changed diff before delivery.
Warden has no veto; do not create an approval loop or a review certificate.

Stop workspace-rooted background writers before promotion: Tollgate may remove
the clean source workspace. Use a durable project directory for the wait.

```sh
hive delivery submit <bead> --project <project> --owner <task> --turn <turn> --json
# Record waiting-for-delivery with returned candidate, source, and workspace.
hive delivery approve <bead> --project <project> --owner <task> --turn <turn>
```

The waiting phase contains `kind`, `workspace`, `source`, and `candidate`. Record
only acknowledged resources through a fresh CLI invocation. Filing normally
authorizes promotion; honor any explicit user approval restriction before
`approve`. Validation alone is not completion.

Invoke the Hive MCP `wait_for_delivery` with the candidate, project, and a
3,600-second deadline. The host must permit at least 3,900 seconds and keep the
call pending. A foreground `hive delivery wait` is also valid when the tool host
can keep it pending without returning intermediate prompts to the model. Do not
poll. If that blocking capability is unavailable, report the integration gap;
repeated short waits are not a substitute. Keep the parent title useful while
waiting and retain its slot.

Accept only a delivered outcome with promotion and configured local/remote
synchronization complete. The installed-provider gap documented in
`docs/tollgate-boundary.md` is not a successful delivery. Use
[repair guidance](repair.md) for CI failures, conflicts, timeouts, or uncertain
results. Never bypass ordinary CI or push worktree branches directly.

For external artifacts, work in `drafting`, then record `reviewing-artifact` with
the artifact location. Run a fresh warden with the artifact, acceptance criteria,
and relevant invariants. Complete with artifact delivery instead of fabricating
a Tollgate candidate. Project-file changes always use the code path above.

## Completion

Before closing the bead:

- Confirm intended behavior, proportionate checks, and review responses.
- Confirm actual delivery or the artifact's acceptance result.
- File/link relevant pre-existing defects, tooling failures or slowness, CI
  failures or slowness, and architecture debt. Do not invent findings.
- Preserve useful source documentation and invariant updates within scope.
- Settle owned writers, record a concise outcome and resource references, and
  call `task complete` with the matching source/candidate or artifact location.

An executor then attempts `task next` in its starting project and repeats.
Weaver delivering an unapproved design instead requests design approval; closing
its authoring bead does not authorize that design's implementation.
