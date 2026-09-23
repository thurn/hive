"""The source-selected command boundary; external effects enter typed adapters."""

import argparse
import json
import sys

from hive.errors import HiveError
from hive.launch_context import LaunchContext


def main() -> int:
    parser = argparse.ArgumentParser(prog="hive")
    parser.add_argument("command", choices=("source",))
    parser.add_argument("--json", action="store_true", help="emit structured output")
    arguments = parser.parse_args()
    try:
        context = LaunchContext.read()
        context.release()
        if arguments.json:
            print(
                json.dumps(
                    {
                        "code": "SourceSelected",
                        "commit": context.commit,
                        "directory": str(context.source),
                    }
                )
            )
        else:
            print(f"Local master {context.commit}\nSource: {context.source}")
        return 0
    except HiveError as error:
        print(
            json.dumps(
                {
                    "code": error.code,
                    "detail": error.detail,
                    "uncertain": error.uncertain,
                }
            ),
            file=sys.stderr,
        )
        return 1
