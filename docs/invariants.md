# Hive project invariants

- Beads is the only task authority. Telemetry and native task titles cannot
  grant ownership, release capacity, or satisfy dependencies.
- Each owned bead consumes one global slot, including deferred work whose
  writers have not settled. Review and delivery keep the parent slot.
- Direct claims and next claims enforce the same project, dependency, ownership,
  and capacity rules under the admission lock.
- Only successful completion satisfies a prerequisite. Cancellation does not.
- A user pause or pending design approval cannot be downgraded by changing its
  reason. Resumption does not discard retained workspace or candidate identity.
- Unfinished work preserves its project's repository and native task binding,
  including queued and settled deferred work without a current assignee.
- Interrupted prerequisite attachment leaves deferred intent; missing required
  edges prevent resumption until they are repaired or explicitly removed.
- Recovery must establish that native owners and writers stopped before its
  owner-and-turn comparison can release a claim.
- A local lock provides exclusion, not rollback of separate database commands.
  Lock files retain permanent identities; process death releases kernel locks.
- Maintenance failure leaves a durable write stop. Concurrent maintenance must
  never start conversion after another operation cleared its stop.
- Ordinary updates change the next invocation while existing operations retain
  consistent source and connections. No dispatcher restarts stopped executors.
- Missing observation, pricing, or usage is unknown, never zero. Observation and
  UI failures cannot block ordinary execution.

The complete acceptance criteria remain in the Hive design. This document names
properties for code review; it does not certify their implementation.
