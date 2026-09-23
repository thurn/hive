"""Short CLI adapter around validated requests and the shared command handler."""

import json
import sys

from hive.cli_display import display
from hive.cli_parser import parse_request
from hive.command_handler import handle
from hive.commands import (
    ApproveWork,
    Change,
    CreateWorkspace,
    SubmitWork,
    WatchCollection,
    mutates,
)
from hive.delivery_commands import prepare_external
from hive.delivery_settlement import prepare as prepare_settlement
from hive.errors import HiveError
from hive.launch_context import LaunchContext


def main() -> int:
    try:
        request, structured = parse_request(sys.argv[1:])
        context = LaunchContext.read()
        effect = None
        if isinstance(request, Change):
            try:
                context.check_mutations_allowed()
                effect = prepare_settlement(request, context)
            except BaseException:
                context.release()
                raise
        if effect is not None:
            context.release()
            result = effect()
        elif isinstance(request, (CreateWorkspace, SubmitWork, ApproveWork)):
            try:
                context.check_mutations_allowed()
                effect = prepare_external(request, context)
            finally:
                context.release()
            result = effect()
        else:
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
        # The resident owns its output stream. A final synchronous write here
        # could block shutdown on an unread supervisor pipe.
        if not isinstance(request, WatchCollection):
            print(
                json.dumps(result, ensure_ascii=False)
                if structured
                else display(result)
            )
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
