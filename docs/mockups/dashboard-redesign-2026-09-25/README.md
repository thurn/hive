# Dashboard design mockups

Three final visual concepts generated with built-in imagegen on September 25,
2026, preserving Hive's dark violet aesthetic while reducing visible copy and
organizing work, cost, and delivery information.

These are design references, not implemented screens or live application data.
Titles, values, counts, and timelines are illustrative; interactions and
responsive behavior have not been tested.

Read the [screen specification](screen-spec.md) for a complete textual description
of the layouts, information hierarchy, visible content, proposed interactions,
and treatment of data not shown in the images. It also identifies generated
details that must not be copied literally. Agents should use that document
instead of inferring requirements from the PNGs.

The work list and card grid are alternatives for the same Newsfeed; the detail
screen complements either. The recommendation is the work list plus the detail
screen, not three new top-level destinations.

## Work list — recommended direction

Aligned task titles, projects, states, and costs make a large collection of work
easier to scan, with descriptions and provenance reserved for task details.

![List-first Newsfeed concept](01-work-list.png)

## Refined cards — alternative direction

A quieter version of the existing card layout, keeping only the project, state,
task title, cost, and role visible on each card.

![Refined card-grid Newsfeed concept](02-refined-cards.png)

## Task detail

A compact spending chart, grouped error events, and a delivery summary put the
overview in one screen, with diagnostics available through a separate tab.

![Focused task-detail concept](03-task-detail.png)
