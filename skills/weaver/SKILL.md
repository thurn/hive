---
name: weaver
description: Write a standalone design and obtain cold review and user approval before implementation.
---

Use [entry](../shared/entry.md) and [filing](../shared/filing.md). Write a standalone design, obtain fresh cold document review, and seek user approval before implementation.

Create a native parent bead with `--type epic` for the whole implementation, or reuse an existing epic covering that same effort. Record the design location, overall scope and whole-implementation acceptance criteria on the epic, and include its ID in the design. File every implementation bead under that epic with `--parent <epic-id>`; attach existing implementation beads with `bd update <id> --parent <epic-id>`. Preserve separate prerequisite edges between children: parenting groups the work and does not specify its execution order. Follow the shared filing sequence so parenting, prerequisites and approval holds are verified before adding `hive_project`.

Keep the epic and every unapproved implementation bead deferred natively without an expiring date until explicit user approval; approval releases only the approved scope, preserving prerequisites. A separate design-authoring bead may close when the document is delivered, but its closure neither approves implementation nor completes the epic. Close the epic only after all implementation children are completed and whole-implementation acceptance is satisfied.
