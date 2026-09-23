"""Short CLI adapter around validated requests and the shared command handler."""

import json
import sys

from hive.cli_display import display
from hive.cli_parser import parse_request
from hive.command_handler import handle
from hive.commands import mutates
from hive.errors import HiveError
from hive.launch_context import LaunchContext


def main() -> int:
    try:
        request, structured = parse_request(sys.argv[1:])
        context = LaunchContext.read()
        mutation = mutates(request)
        if not mutation:
            context.release()
        try:
            if mutation:
                context.check_mutations_allowed()
            result = handle(request, context)
        finally:
            if mutation:
                context.release()
        print(json.dumps(result, ensure_ascii=False) if structured else display(result))
        return 0
    except HiveError as error:
        result = {
            "code": error.code,
            "detail": error.detail,
            "uncertain": error.uncertain,
        }
        output = (
            json.dumps(result, ensure_ascii=False)
            if "--json" in sys.argv[1:]
            else f"{error.code}: {error.detail}"
            + (
                "\nOutcome uncertain; inspect before retrying."
                if error.uncertain
                else ""
            )
        )
        print(output, file=sys.stderr)
        return 1
