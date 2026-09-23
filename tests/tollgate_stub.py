"""Subprocess protocol fixture; it does not simulate provider validation."""

import fcntl
import json
import os
import sys
import time
from pathlib import Path

from hive.jsonvalue import integer, parse, record, string


def main() -> int:
    root = Path(__file__).resolve().parent
    args = sys.argv[1:]
    if args[:2] != ["--no-launch", "--json"]:
        raise AssertionError("Missing non-launching structured provider boundary")
    args = args[2:]
    with (root / "calls.jsonl").open("a") as output:
        output.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
    if any(key.startswith("GIT_") for key in os.environ):
        raise AssertionError("Ambient Git routing leaked into provider")
    plan = record(parse((root / "fixture.json").read_text()))
    locks = plan.get("locks")
    if isinstance(locks, str):
        for name in ("admission.lock", "maintenance.lock"):
            with (Path(locks) / name).open("a") as guard:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    reply = record(plan.get(args[0]), "fixture command")
    entered = reply.get("entered")
    if isinstance(entered, str):
        Path(entered).touch()
    time.sleep(integer(reply.get("delay", 0), "delay"))
    print(string(reply.get("output"), "output"), flush=True)
    return integer(reply.get("exit", 0), "exit")


if __name__ == "__main__":
    raise SystemExit(main())
