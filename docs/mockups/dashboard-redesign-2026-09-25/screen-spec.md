# Dashboard mockups: text specification

This document describes the three [mockups](README.md) without requiring image
interpretation. Its goal is a calmer Hive dashboard that keeps the useful
information and makes the default presentation much smaller.

This is a design handoff, not evidence of implemented behavior, a live visual
review, or authorization to implement the redesign. Layout and interaction
recommendations below are proposed. Existing accounting, identity, security,
and read-only behavior from the [dashboard plan](../../dashboard-plan.md) and
current application remain constraints. The original plan's visual layout is
the baseline being reconsidered; its data guarantees are not being replaced.

When a generated image conflicts with this text, use the text for design intent.
Do not infer functionality, precise measurements, or real data from the image.

## 1. Direction and screen relationships

The primary question on the Newsfeed is: **What work is happening, what state is
it in, and what has it cost?** The detail view answers: **What contributed to
this amount, what happened, and where can I inspect the evidence?**

| Image | Purpose | Recommendation |
|---|---|---|
| `01-work-list.png` | Newsfeed as aligned rows | Preferred default for scanning many tasks |
| `02-refined-cards.png` | Newsfeed as six restrained cards | Alternative if keeping the existing card metaphor |
| `03-task-detail.png` | Overview of one selected item | Shared detail destination for either feed design |

The two feed concepts are alternatives, not a requirement to add a view switch.
Keep the existing feed and bead/session/ledger detail routes; do not make the
detail view a modal or turn every section into a new sidebar destination.

Attention should move from page title, to the compact spending summary, to work
titles and their states. The work occupies most of the useful page area. On a
detail page, attention moves from task identity and state, to spending over time,
to delivery, then to evidence when requested.

The main subtraction is whole categories of default-visible content: description
excerpts, source paths, hashes, repeated emblems, diagnostic badge clusters,
large explanatory headings, and expanded logs. Moving these behind a clear
details action is essential; merely shrinking all of them is not the design.

## 2. Shared shell and visual language

### Page structure

On a wide desktop window, use a narrow left navigation rail and a main content
column. The rail extends down the page and has one faint right separator. Its
background is the same near-black violet as the canvas, rather than a contrasting
panel. The main column has generous, consistent left and right insets.

The rail contains, in order:

1. The existing Hive hexagonal mark and wordmark.
2. A Newsfeed navigation item.
3. A quiet Projects label.
4. All projects, then the actual registered projects, with Other when applicable.
5. A small Local observation footer; this is informational, not an account menu.

Selected navigation uses a restrained violet-tinted surface with a clear label.
Avoid large project counts and a separate outlined emblem beside every entry.
Project navigation changes the project scope of the feed. Retain the scope while
opening details, and restore feed filters and scroll position when going back.

### Type, spacing, and surfaces

Preserve the existing color roles: near-black violet canvas, slightly lighter
surfaces, off-white primary text, muted gray secondary text, and lavender for
brand, selection, links, and focus. Green identifies working/completed states,
blue identifies CI, and amber identifies attention or incomplete coverage.
Keep actual failures distinguishable from warnings. Every state has a text label;
color alone does not convey it.

Use the existing design tokens as the starting point. The following dimensions
are proposed layout targets, not measurements extracted from the PNGs:

| Element | Intended treatment |
|---|---|
| Desktop rail | Approximately 200–224 CSS px; subordinate to the work area |
| Main content inset | 32–48 px on a wide window; 16–24 px when narrow |
| Page title | Approximately 28–32 px, semibold; no eyebrow or slogan above/below |
| Main spend amount | Approximately 36–40 px; tabular digits, without heavy decoration |
| Row/card title | Approximately 16–18 px, strongest text within the item |
| Metadata and controls | Approximately 14–16 px; do not make secondary copy tiny |
| Vertical rhythm | Reuse the existing 4/8/12/16/24/32/48 spacing scale |
| Cards | Flat, subtly lighter fill; one faint outline; roughly 12 px radius |
| Controls | One shared radius and border treatment; balanced internal padding |

Use whitespace to group content before adding panels. The header, summary,
toolbar, table, and chart do not each need their own rounded outer container.
Reserve stronger surfaces for a selected row, an open control, and a small number
of genuinely separate sections. No glows, ornamental gradients, or large shadows.
Retain Hive's actual logo and icons; generated variations are not a rebrand.

## 3. Shared Newsfeed header and summary

At the top of the main column, put Newsfeed at the left and a compact date-window
control at the right. The mockup shows Last 7 days; retain Today, 7 days, and
30 days with the API's reported timezone.

Immediately below, show an unboxed spending summary. Its left group is a small
Recorded spend label, a prominent amount, and an adjacent or directly following
Coverage incomplete action when applicable. The example amount is $1,404.25.
Remove the old slogan and the large, separate coverage-warning carousel.

The summary amount is for the selected time window. Work amounts below it are
lifetime amounts. Use a Lifetime spend column heading in the list, and a single
quiet Lifetime amounts caption for the card grid, so the date control does not
imply that all visible amounts use that window. Do not sum the current page and
present that as the fleet total. Preserve the API's existing summary/filter scope;
do not silently redefine it as the total of locally visible rows.

Both feed alternatives retain windowed role analysis through a Spend by role
action beside the summary. It reveals all role amounts from `summary.roles` for
the selected window. The list uses this compact disclosure; the card alternative
also previews those amounts in its strip. Task-detail lifetime breakdowns do not
replace this fleet-level, windowed view.

Coverage remains visible beside the affected number. The pictures use a trailing
plus, but the implementation should retain Hive's established `≥` convention for
an incomplete estimate rather than introducing an ambiguous new money format.
Keep unpriced request counts available in the coverage disclosure. A recorded
zero with missing coverage must not look like a verified free task.

The coverage action opens a concise explanation of missing or unpriced coverage
and the affected scope. It must be usable by activation, not hover alone. Keep
the API-equivalent estimate qualification available here and through a quiet
shared amount note, rather than repeating a paragraph on every row.

The original dashboard also has non-coverage hotspots, such as CI churn and
retry loops. Do not delete those findings because their banner is absent from
the drawing. Proposed treatment: a small contextual Issues action beside the
summary opens the ranked findings and links to affected work. Avoid duplicating
the same coverage notice in two places; show no empty decorative Issues panel.
Observation outages and stale data remain visible independently of cost coverage.

### Differences between the two summary drawings

The list drawing places a small lavender sparkline after the amount, then
secondary counts reading 12 active and 8 in CI. These are illustrative placeholders:
the current feed payload does not provide that trend or authoritative aggregate
state counts. Omit them until a correctly scoped data source exists; do not
estimate fleet counts from one loaded page or manufacture a trend. Their omission
should make the summary narrower, not leave empty metric boxes.

The card drawing instead places a thin spending-by-role strip at the right:

| Role group | Illustrative amount |
|---|---:|
| Executor | $1,145.58 |
| Weaver | $175.24 |
| Other roles | $83.43 |

Segments represent exact shares of the selected window's recorded total, with
labels below. Other roles aggregates all remaining roles; it is not a new
accounting category and is unrelated to the Other project group. Keep the full
role breakdown available on activation. Widths come from amounts, not the pixels
in the image; preserve exact totals before display rounding.

## 4. Newsfeed toolbar and behavior

Below the summary, leave a clear vertical gap, then one aligned toolbar. The left
side contains the work selection; the right contains Search work and Filters.
The drawing shows All work, Active, and Completed as tabs. All work is selected.
Selected tabs use a simple lavender underline; use that same treatment on detail
tabs instead of copying the card drawing's extra segmented outlines.

The tabs are proposed shortcuts, not new source states. Active must be labeled
Active in window if it invokes the existing activity-window filter; it must not
quietly mean only Working or imply that In CI is excluded. Completed has no
established grouped query contract in this mockup: do not silently equate Complete,
Finished, Closed, and Cancelled. Until that grouping is explicitly defined, retain
the existing exact state filter inside Filters instead of inventing a tab mapping.
The layout also works with a simple All work heading in place of extra tabs.

Filters reveals the existing role, exact state, activity-window, and older-completed
controls. Project scope is available through the rail and may also be shown in
the filter panel when useful in narrow layouts; it is one shared value. Show a
small applied-filter count and a clear reset action. Do not keep an entire second
row of controls and helper text open by default. All work retains the current
older-completed exclusion unless that filter is changed.

Search and filters use the existing query semantics and reset the pagination
cursor when the query changes. Keep last-activity ordering and stable item keys.
The pictures' numbered pagination, page totals, and Showing 7 of 290 text are
composition placeholders. The current feed uses a cursor, so use Load more work
unless authoritative totals and page navigation are separately provided. Preserve
coherent refresh of all loaded pages; do not leave stale rows mixed into new data.

## 5. Screen 01: work list

The screen reads from top to bottom as:

```text
Shared rail | Newsfeed                                  [Last 7 days]
            | Recorded spend
            | ≥ $1,404.25   Coverage incomplete
            |
            | All work                  [Search work]      [Filters]
            | ----------------------------------------------------
            | Work                  Project     State   Lifetime spend
            | Task title            project     state           amount
            | Task title            project     state           amount
            | ...
            |                                [Load more work]
```

The work table is the dominant region. It has four columns: Work, Project, State,
and Lifetime spend. Give Work roughly half the available table width, allow Project
and State enough room for real labels, and keep amounts in a right-aligned final
column. Align all row baselines. Light horizontal rules separate rows; there are
no vertical grid lines or individual rounded card containers.

Each row normally contains only a title, project, labeled state dot, and amount.
No description preview, source path, primary-role emblem, task ID subtitle, warning
count cluster, or trailing arrow. Hover gives the whole row a subtle raised fill;
pressed and keyboard-focus states must also be distinct. The tinted row in the
image demonstrates hover/focus emphasis, not a new multiselect feature.

The title is the primary detail link. The row can provide the same navigation
target, but must not make nested links or controls behave unpredictably. The
destination remains a full detail page. No checkboxes, bulk actions, editing,
claiming, or status mutation are implied.

The illustrative rows, in order, are:

| Work | Project | State | Lifetime spend, incomplete |
|---|---|---|---:|
| Repair CI bottlenecks | battlement | In CI | ≥ $130.48 |
| Compare replay scopes | battlement | Working | ≥ $2.71 |
| Exclude admission from watchdog | battlement | Working | ≥ $8.69 |
| Validate source identity | hive | In CI | ≥ $4.36 |
| Correct archive eligibility | hive | Complete | ≥ $22.65 |
| Apply crash reporting controls | battlement | Complete | ≥ $182.07 |
| Improve build diagnostics | battlement | Complete | ≥ $18.42 |

These shortened titles demonstrate density, not an instruction to rewrite stored
titles or summarize them with a model. Use actual source titles. Allow a second
line when needed; disclose the full title on focus/activation if visually clamped,
and always show it in the detail header. Rows may grow for real content. Long state
labels and large money values must not collide with neighboring columns.

Bead, session, tail, and ledger items all remain in this presentation. For a
non-bead item whose title would be ambiguous, allow one short type qualifier such
as Session, Unowned tail, or Unattributable spend. Do not disguise ledger entries
as ordinary completed tasks or drop them to make the page look tidy.

## 6. Screen 02: refined cards

This has the same rail, title, date control, summary purpose, and toolbar as the
list. Its main difference is a three-column, two-row grid in the wide mockup.
Use moderate gaps between cards and consistent outer edges. Each card is a flat,
slightly lighter violet surface with one very faint outline and modest rounding.

Each card has precisely this hierarchy:

```text
project                                  ● State

Task title, allowed to occupy two lines

≥ $amount                                   Role
```

Use approximately 24 px internal padding. The project and state sit on one top
line, the title begins below that line, and the amount/role footer aligns across
sibling cards. Let the title occupy the space it needs; do not force every card
to be a large empty rectangle. There is no internal divider above the footer.

The six cards use the first six rows in the Screen 01 example table, arranged
left-to-right then top-to-bottom. Their illustrative role is Executor. If actual
spend has multiple request roles, show a concise primary role plus a disclosure
for the others; do not imply every dollar belongs to the primary role. The detail
breakdown supplies the exact role shares.

Remove the large project emblem, description excerpt, bead ID subtitle, cluster
of diagnostic badges, and arrow from the old cards. A card should usually have
five text groups: project, state, title, amount, and role. The old example had
roughly 65–70 words; the shortened examples here have roughly 12–16. Real titles
may be longer, so this is a density direction rather than a word-count limit.

The entire card may navigate to detail with coherent hover, pressed, and focus
treatments. Keep the same real data, ordering, coverage signals, non-bead types,
and query behavior as the list concept. Do not implement a second accounting
path for the alternate layout.

## 7. Screen 03: task-detail overview

### Header and overall layout

Keep the shared rail. At the top of the main column, show a compact breadcrumb
back to Newsfeed and the project. Below it, place the task's labeled state, then
its full title. The example title is Apply crash reporting controls, with a
Complete state. A quiet metadata line contains the actual bead/session identity,
role summary, and contributing-session count.

The drawing's `hv-5h3` and timeline are illustrative and are not a verified
association with that title. Never copy them into actual task metadata.

An Open session link sits at the right when there is exactly one clear session
destination. It opens the existing session detail route; it is not a resume
command. Multiple contributors require a Sessions action leading to a chooser
or contributor list, rather than choosing an arbitrary session. Omit the action
when there is no valid destination.

Below the header, use Overview, Diagnostics, and Delivery tabs with a fine
divider continuing across the page. The example badges 152 and 2 are illustrative;
counts must match the actual content they name. The large title may wrap, and
the action moves below it when needed rather than competing for the same space.

On a wide window, the overview body is roughly two-thirds chart/activity and
one-third delivery/supporting facts. Use a clear gutter, not multiple heavy
vertical rules. At about 1440 × 900 CSS px, the target is to see the header,
chart, delivery summary, and the beginning of recent activity without a giant
chart consuming the entire viewport. This is a proposed target, not a verified
responsive result; longer content is allowed to extend the page.

### Spending chart

In the left column, start with Recorded spend, a prominent lifetime amount, and
its coverage link. The example is ≥ $182.07. Below it is a moderately tall
cumulative chart, roughly 220–280 CSS px on a wide layout, rather than a giant
panel with a largely empty plot.

Use a thin lavender line, restrained horizontal grid lines, a few dollar-axis
labels, and readable time ticks. The drawn example runs from 9 PM to 10 AM and
has ticks near $0, $90, and $180. Actual domains and the final point must come
from the data; provide headroom so the top point is not clipped. Dates and the
reported timezone must be available, especially across midnight. Do not animate
or smooth an illustrative curve into apparent evidence.

The line represents the item's actual attributable scope. Preserve ownership
boundaries and make excluded context explicitly distinguishable if shown. Never
add outside-ownership context to a bead's total. Keep detailed ownership bands
available through the supporting details described below, instead of permanently
labeling every band on the compact overview.

Brushing/selecting a time interval is useful, but scope changes must be explicit:
show a Selected range indicator and Clear range action. Range-filtered diagnostic
and request lists use that interval; the headline amount and breakdown remain
lifetime unless separately labeled as a selected-range total. The chart must not
silently change what the prominent amount means.

### Diagnostic density strip

Place a compact event-density strip beneath the time axis. The example is Tool
errors, using a dozen or so subdued bins instead of dozens of overlapping red X
marks. Bins share the chart's time domain and show counts, not dollar amounts or
severity inferred from height. Do not interpret a missing observation as a
verified zero-error interval.

One selected bin has a visible outline and a concise contextual label, such as
12 events · 3:00–4:00 AM. Activating it opens Diagnostics filtered to that interval
and event kind. Grouping only changes the overview rendering; all underlying
events remain inspectable. Keep labels and selection understandable without
color alone. Other diagnostic kinds remain available in Diagnostics rather than
adding a separate visible strip for each kind by default.

### Recent activity

Below the strip, show a small Recent activity list with approximately three
meaningful milestones. Each row has an event label on the left and a timestamp
on the right, separated from the next row by a faint rule. Example labels are
Promoted to master, Checks passed, and Submitted for checks.

Use observed records only. Submission is neutral, passed checks are positive,
and a failure must retain its failure treatment; do not copy the image's green
dots onto every kind of activity. Source-derived activity may be omitted if the
current data cannot establish it. A single successful delivery does not prove
the bead is complete.

### Right column

The top block is a compact Delivery summary on a slightly raised surface. It
shows a delivery state, Checks result, Time to promote when known, and View
delivery. The example says Promoted, Passed, and 30 min. Use the joined candidate
state and observed timestamps. If several candidates matter, show a concise
multi-candidate summary and expose them all through Delivery; do not hide an
unsuperseded failure behind a successful sibling candidate.

Below, an unboxed Spend by role section shows each relevant role and amount.
For the single-role example, Executor and $182.07 are one compact fact row; a
thin proportional accent may accompany it. A giant 100% bar is unnecessary.
Multiple roles use a compact breakdown with real shares. View all breakdowns
reveals the existing dimensions rather than losing them for visual simplicity.

The final quiet action, View task description, opens the read-only task record.
The supporting facts are subordinate to the chart and delivery state, but remain
easy to discover. Do not present a small role list as the only available evidence
for the total.

## 8. Details intentionally absent from the overview image

The following is proposed disclosure behavior, not additional rendered mockups.
It specifies where existing capabilities go so that an agent does not interpret
an uncluttered picture as authorization to remove them.

| Information | Proposed destination and content |
|---|---|
| Diagnostics | Diagnostics tab: event-kind filters, counts for the stated scope, range control, and rows with event, time, session/subagent, relevant duration or amount, and an on-demand Excerpt action |
| Delivery evidence | Delivery tab: every relevant candidate, state, subject, branch, match method, submission/promotion times, attempts and steps, with log tails tied to the exact attempt |
| Requests | View requests near the chart/breakdown opens an inline section with the existing paged request table, amounts, and selected-range scope |
| Full breakdowns | View all breakdowns opens the existing request-role, thread, subagent, model, token-category, skill, query-source, and tool dimensions; retain the estimated qualification for tool allocation |
| Ownership | Ownership details exposes interval bounds, linked beads/sessions, and excluded-context explanations |
| Contributors | Sessions/contributors exposes all owning or contributing sessions and their scoped amounts, role spans, subagents, and inherited-role indicators |
| Beads record | View task description expands the description, then provides grouped read-only fields, acceptance criteria, notes, metadata, dependencies, dependents, comments, events, and existing copyable commands |
| Session/ledger specifics | Preserve filed-work links, tail scope, ledger contributors and reasons, and missing/deleted identity explanations where applicable |

These supporting sections expand below the overview or replace its main body
when a named tab is selected; do not layer a series of nested cards over the
chart. Direct task routes remain reloadable. Going back returns to the prior
feed state. Keep task identity and the selected diagnostic interval visible
enough that a user knows which evidence they are reading.

Excerpts and logs are requested on demand. Preserve bounded reads, unavailable
reasons, source identity validation, and no-store behavior. Preserve sanitized
read-only Beads rendering, cached-record age, and copyable commands; do not add
browser writes or run commands because a mockup contains an action-shaped link.

## 9. Missing data, long content, and small windows

These states were not rendered in the PNGs and have not been visually tested.
They are required considerations for an eventual implementation:

| Condition | Intended presentation |
|---|---|
| Initial loading | Stable header and modest loading placeholders where work will appear; no fake $0 total |
| Empty filtered feed | No work matches with a clear way to reset filters; retain visible filter scope |
| No observed work | Short explanation that no observations are available; do not invent summary metrics |
| Refresh failure with prior data | Retain the readable prior view with a compact stale/unavailable notice and actual freshness information |
| No usable snapshot | Explicit unavailable state and retry action, rather than an empty success screen |
| Incomplete or unpriced cost | Keep the partial amount, its qualification, and access to coverage details together |
| No delivery | No observed delivery; do not infer promotion or add a decorative empty success card |
| Missing excerpt/log | Explain that the source cannot be read; keep the surrounding evidence and identity |
| Long title/state or large amount | Let content wrap or reflow intentionally; never overlap, shrink to tiny type, or truncate the money value |
| Many contributors/candidates | Compact count plus a complete expandable list; do not show a fabricated singular owner or latest-success-only summary |

Narrow layouts serve small desktop windows; these mockups do not propose remote
or phone access. At reduced widths, move date/search/filter controls onto a
second line before compressing labels. Collapse the rail to a compact navigation
control when it would crowd the content. The card alternative becomes two columns
then one, based on content fit.

For the list, a stacked row is preferable to crushing four columns: full-width
title, followed by project and state on the left and amount on the right. In the
detail view, stack the supporting column below the chart; keep chart axes and
disclosures legible. Avoid fixed page heights and hidden overflow. Check natural,
wide, narrow, and intermediate/tall windows, plus zoom and realistic long content,
before claiming the design works responsively.

## 10. Guardrails for an implementing agent

Treat the following as checks on interpretation, not completed acceptance:

- Implement the chosen feed composition and shared detail view, not every visual
  variation or hypothetical shortcut simultaneously.
- Make work titles, states, and lifetime costs immediately comparable; keep the
  summary window unmistakably separate from lifetime amounts.
- Keep bead, session, tail, and ledger accounting intact, including unknown
  attribution, partial coverage, and exact role/breakdown totals.
- Preserve all existing states and error evidence even when they are absent from
  the sample rows; a quiet default must not conceal failures.
- Use actual titles, identities, amounts, timestamps, and candidate associations;
  never copy mock data into production or infer absent aggregate APIs.
- Keep diagnostics, requests, delivery attempts, source evidence, and the full
  Beads record reachable through clear, named disclosures.
- Reuse actual Hive tokens, brand assets, routes, and source semantics; reconcile
  minor icon, sidebar, and tab differences between generated images.
- Verify hover, pressed, focus, loading, error, empty, and long-content states in
  the running UI, including control padding, containment, contrast, and overflow.
- Preserve local-master hot reload, consistent assets within a running call,
  resident connection continuity, and the read-only security boundary.

No live UI measurements, responsiveness checks, or interaction acceptance are
claimed by this documentation change. The PNGs remain unchanged visual references.
