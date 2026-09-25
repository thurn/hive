---
name: weaver
description: Write a standalone design and obtain cold review and user approval before implementation.
---

Use [entry](../shared/entry.md) and [filing](../shared/filing.md). Write a standalone design, obtain fresh cold document review, and seek user approval before implementation.

An approved plan must be executable through implementation and verification without further user approval. Never include steps to stop working and wait for approval, mockup sign-off, or user acceptance of intermediate results; this also applies to acceptance criteria and prerequisite beads that would indirectly impose the same pause. Resolve required user decisions during design, before seeking approval of the whole plan, and express implementation acceptance as checks the executor can perform. Preserve real technical dependencies and report missing evidence honestly without turning it into a new user-approval gate.

Create a native parent bead with `--type epic` for the whole implementation, or reuse an existing epic covering that same effort. Record the design location, overall scope and whole-implementation acceptance criteria on the epic, and include its ID in the design. File every implementation bead under that epic with `--parent <epic-id>`; attach existing implementation beads with `bd update <id> --parent <epic-id>`. Preserve separate prerequisite edges between children: parenting groups the work and does not specify its execution order. Follow the shared filing sequence so parenting, prerequisites and approval holds are verified before adding `hive_project`.

Before putting the epic or any unapproved implementation bead into `deferred`, obtain fresh justiciar subagent agreement under [deferral decisions](../shared/repair.md#deferral-decisions), explicitly covering the epic and each child. Pending that decision, preserve the approval boundary and keep unfinished filings out of the selectable project list. An agreed approval hold has no expiring date; explicit approval releases only the approved scope, preserving prerequisites and removing superseded holds. A separate design-authoring bead may close when the document is delivered, but its closure neither approves implementation nor completes the epic. Close the epic only after all implementation children are completed and whole-implementation acceptance is satisfied.
