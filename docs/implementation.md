# Implementation and acceptance status

The authoritative product contract is [scope reduction](scope-reduction-plan.md). The former Fulcrum design and Hive admission/session/delivery assertions are historical. Their deletion retires those requirements; it does not count as implementing them.

## Implemented in this branch

- Native Beads routing-only launcher, with explicit configured server, sanitization, and override rejection.
- Source-selected local-master calls and immutable running-call snapshots.
- Bead-linked creator/assignee worklist including closed work, cached discovery across outages, and thread-level incremental usage/cost associations.
- Native role instructions, simple skill links, reduced fast/full gates, and manual operations guidance.

## Acceptance still required

- Tollgate's native completion output was checked against a disposable two-candidate journey. Its missing local-master distinction is being corrected in the Tollgate provider repository; that candidate still requires its repository's explicit promotion approval. Until it lands, native candidate status plus actual local-master inclusion remain the manual completion check.
- The disposable two-bead executor journey, distinct real-thread competing claim, native backup/restore round trip, real linked-thread observation comparison, collector restart and conservative active-thread archive skip passed. The one-time native 30-minute wait still needs its terminal result; source selection advanced in the disposable clone while it was pending. No eligible disposable thread has been archived. See `docs/acceptance.md` for evidence and limits.
- Three quiescent runs passed the 30-second fast and 120-second full budgets; timings are in `docs/acceptance.md`.
- The opt-in collector command and stop/restart behavior were validated with disposable configuration; no host service was installed. Production activation and Fulcrum work migration remain separate operator actions.

No assembled acceptance or production activation is implied by unit tests. Missing native activity evidence means archivist skips archival.
