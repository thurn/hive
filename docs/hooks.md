# Stop reminders and explicit interruption

`~/hive/bin/hive hook` accepts one native JSON event on stdin and returns native
hook JSON on stdout. The supported events are `Stop` and `Interrupt`. It uses
the event's `session_id` and `turn_id`; it never guesses identities from titles,
the current shell directory, or transcript timestamps.

The wire fields and hook trust flow are documented in the
[released Codex hook reference](https://learn.chatgpt.com/docs/hooks). This
implementation has portable subprocess and real-Beads tests. Native event
delivery, user trust, deadline behavior, and continuation identity still need
verification in the installed desktop runtime. The tests do not establish those
capabilities, and no live hook configuration is installed by development checks.

## Reminder behavior

A normal stop reads enrollment, project scope, dependencies, and capacity. Only
an enrolled executor with apparently eligible work and room to claim it can
receive a reminder. The hook checks the same admission predicates as a real
claim, but writes no ownership, starts no worker, and makes no resource decision
for the executor. Conditions can change before the executor's actual claim.

The local reminder guard atomically records a reminder before emitting it.
Another process or repeated callback cannot emit it again for the same native
task, turn, and last observed completion. A successful completion permits one
new reminder. A native continued-stop event with no completion observed in its
turn is allowed to stop, preventing an accidental chain of fresh reminders if
the runtime changes turn identity during continuation.

Full capacity, blocked dependencies, existing ownership, and a maintenance stop
allow stopping without consuming the reminder. Unknown data or a damaged guard
also allows stopping, with a diagnostic. The hook does not attempt automatic
recovery or wake stopped executors later.

The reminder database has a 50ms busy deadline. Concurrent callbacks may reach
that deadline; unavailable guard state permits stopping with a diagnostic and
must not request continuation. Tests hold a real SQLite write transaction to
exercise this path, then verify that competing processes emit exactly one
reminder after contention clears. Unexpected child-process failures retain
their stderr rather than being reported only as an exit code.

Completion observations are written under the existing admission exclusion
after the acknowledged Beads transition. This preserves their order without a
second work ledger. If the observation cannot be saved, the task operation still
succeeds and reports a warning. A crash between stores can lose a reminder
reset; it cannot undo completed work or grant ownership.

## Interruption behavior

An interruption first records its native owner pair locally, then tries to
defer that pair's current bead with a user-pause condition under admission
exclusion. Other pause conditions and retained work remain intact. It retains
capacity; it does not claim that processes, reviews, or Tollgate have stopped.
A callback for an older turn cannot pause a later owner.

The native observation survives broken Beads routing or a temporary write
failure. A later `task enter-turn` checks it against the recorded owner, applies
the user pause, and refuses continuation. This is replay of the observed stop,
not authority to recover writers. Normal explicit resumption resolves the
corresponding observation after Beads accepts it. Writer settlement and the
other release conditions still apply.

The hook uses a 100ms admission-lock deadline and 500ms per Beads request. A
timeout remains an uncertain result that requires inspection. A corrupt local
interruption record refuses turn transfer; it does not silently erase the stop.
Native recovery and process-inventory integration remain separate unfinished
requirements. In particular, the hook cannot cancel already-authorized
promotion or prove external writers have settled.

## Configuration and remaining native acceptance

This is the intended user-reviewed configuration, using the canonical launcher:

```json
{
  "hooks": {
    "Stop": [{"hooks": [{
      "type": "command", "command": "~/hive/bin/hive hook", "timeout": 3
    }]}],
    "Interrupt": [{"hooks": [{
      "type": "command", "command": "~/hive/bin/hive hook", "timeout": 3
    }]}]
  }
}
```

Do not replace existing hook configuration or trust records to activate this.
Prepare the combined configuration, review its exact definitions through the
native trust UI, and test it in a disposable Hive-managed conversation before
enabling ordinary work. No per-tool hook is needed for this behavior.

Acceptance still needs actual native Stop/Interrupt event capture, a reminder
followed by real work and a second stop, a user interruption during a delivery
wait, and the three-second deadline under contention. Native activity inspection
must also call the guard's `discard(owner)` after confirming a turn has ended;
automatic pruning is not connected yet. Never prune by elapsed time or reset a
live turn's guard merely to obtain another reminder.
