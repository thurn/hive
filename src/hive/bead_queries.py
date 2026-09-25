"""Only fixed, validated, read-only queries cross the native SQL boundary."""

import re

from hive.errors import ErrorCode, HiveError

FIRST = "00000000-0000-7000-8000-000000000000"
PAGE = 500
UUID7: re.Pattern[str] = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
BEAD: re.Pattern[str] = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
COLUMNS = """SELECT id, issue_id, event_type,
  CASE WHEN JSON_VALID(old_value) THEN JSON_UNQUOTE(JSON_EXTRACT(old_value,'$.status')) END AS old_status,
  CASE WHEN JSON_VALID(old_value) THEN JSON_UNQUOTE(JSON_EXTRACT(old_value,'$.assignee')) END AS old_assignee,
  CASE WHEN event_type <> 'closed' AND JSON_VALID(new_value)
       THEN JSON_UNQUOTE(JSON_EXTRACT(new_value,'$.status')) END AS new_status,
  CASE WHEN event_type <> 'closed' AND JSON_VALID(new_value)
        AND JSON_CONTAINS_PATH(new_value,'one','$.assignee')
       THEN CASE WHEN JSON_TYPE(JSON_EXTRACT(new_value,'$.assignee')) = 'NULL' THEN ''
                 ELSE JSON_UNQUOTE(JSON_EXTRACT(new_value,'$.assignee')) END END AS new_assignee,
  created_at, TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS server_offset
FROM events"""


def page(after: str) -> str:
    if UUID7.fullmatch(after) is None:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid Beads event cursor")
    return f"{COLUMNS} WHERE id > '{after}' ORDER BY id LIMIT {PAGE}"


def bead(identifier: str) -> str:
    if BEAD.fullmatch(identifier) is None:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid bead ID")
    return f"{COLUMNS} WHERE issue_id = '{identifier}' ORDER BY id"
