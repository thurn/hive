"""A live SQL view over request observations and retained price components."""

import json
import sqlite3

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, sequence, string
from hive.pricing import Quote
from hive.usage import tokens

VIEW = """CREATE VIEW request_detail AS
WITH tiers(tier) AS (VALUES ('standard'),('fast'),('batch'),('flex')),
base AS (
 SELECT r.*, t.tier AS selected_tier,
 CASE WHEN r.host='claude' THEN r.model ELSE m.model END AS priced_model,
 m.conflicted,
 CASE WHEN m.conflicted=1 OR EXISTS (SELECT 1 FROM json_each(r.flags) WHERE value='unsupported_iteration')
 THEN NULL ELSE e.quote END AS quote,
 a.agent_type
 FROM responses r
 JOIN tiers t ON r.host='codex' OR t.tier='standard'
 LEFT JOIN turn_models m ON r.host='codex' AND r.task=m.task AND r.turn=m.turn
 LEFT JOIN claude_agents a ON r.host='claude' AND r.task=a.task AND r.agent=a.agent
 LEFT JOIN response_estimates e ON r.response=e.response
 AND e.tier=CASE WHEN r.host='claude' THEN r.modifier_key ELSE t.tier END
)
SELECT host, task AS thread, response, NULL AS source, observed AS observed_at,
 CASE WHEN host='claude' THEN turn END AS prompt_id,
 CASE WHEN host='codex' THEN turn END AS turn,
 agent, agent_type, NULL AS parent_agent, NULL AS query_source, skill,
 NULL AS plugin, NULL AS mcp_tool, priced_model AS model,
 CASE WHEN host='claude' THEN modifier_key ELSE selected_tier END AS modifier_key,
 CASE WHEN host='codex' THEN selected_tier END AS tier,
 NULL AS effort,
 input-cached-cache_write-cache_write_1h AS uncached_input,
 cached AS cache_read, cache_write AS cache_write_5m, cache_write_1h,
 output, reasoning AS thinking_output,
 CASE WHEN host='claude' THEN json_extract(modifiers,'$.web_searches') END AS web_searches,
 json_extract(quote,'$.components_usd.input') AS usd_input,
 json_extract(quote,'$.components_usd.cache_read') AS usd_cache_read,
 json_extract(quote,'$.components_usd.cache_write_5m') AS usd_cache_write_5m,
 json_extract(quote,'$.components_usd.cache_write_1h') AS usd_cache_write_1h,
 json_extract(quote,'$.components_usd.output') AS usd_output,
 json_extract(quote,'$.components_usd.server_tools') AS usd_server_tools,
 json_extract(quote,'$.usd') AS usd, NULL AS host_cost_usd,
 CASE WHEN complete=0 THEN json_insert(flags,'$[#]','possibly_partial') ELSE flags END AS flags,
 usage AS _usage, quote AS _quote, modifiers AS _modifiers, conflicted AS _conflicted
FROM base
"""


def create(connection: sqlite3.Connection) -> None:
    # Backfill exact component strings from retained rates, never today's card.
    # SQLite numeric multiplication can overflow into floating point; the view
    # reads decimal strings computed in Python's arbitrary-precision integers.
    cursor = connection.execute(
        "SELECT e.response,e.tier,e.quote,r.usage FROM response_estimates e "
        "JOIN responses r ON r.response=e.response WHERE r.usage IS NOT NULL"
    )
    while True:
        fetched: object = cursor.fetchmany(256)
        batch = sequence(fetched, "price component migration")
        if not batch:
            break
        for raw in batch:
            if not isinstance(raw, tuple) or len(raw) != 4:
                raise HiveError(ErrorCode.INVALID_RECORD, "Invalid price component row")
            response, tier, value, usage = raw
            quoted = Quote.read(parse(string(value, "retained price")))
            parsed = tokens(parse(string(usage, "retained usage")))
            connection.execute(
                "UPDATE response_estimates SET quote=? WHERE response=? AND tier=?",
                (json.dumps(quoted.value(parsed)), response, tier),
            )
    connection.execute(VIEW)
