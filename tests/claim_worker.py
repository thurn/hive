"""Independent claimant used by the real-server contention test."""

import json
import sys
from pathlib import Path

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.errors import HiveError
from hive.identity import BeadId, CodexTaskId, CodexTurnId, ProjectId
from hive.locking import Guards
from hive.model import Owner
from hive.task_service import TaskService


def main() -> None:
    owner = Owner(CodexTaskId(sys.argv[3]), CodexTurnId("claim-turn"))
    store = BeadsStore(
        BeadsProcess(BeadsConnection.read(Path(sys.argv[1])), owner.task)
    )
    service = TaskService(store, Guards(Path(sys.argv[2])))
    print("ready", flush=True)
    sys.stdin.readline()
    try:
        identifier = None if sys.argv[4] == "next" else BeadId(sys.argv[4])
        bead = service.claim(ProjectId("search"), owner, identifier)
        print(json.dumps({"code": "Claimed", "id": bead.id}), flush=True)
    except HiveError as error:
        print(
            json.dumps({"code": error.code, "uncertain": error.uncertain}), flush=True
        )


if __name__ == "__main__":
    main()
