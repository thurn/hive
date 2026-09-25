"""A live SQL view over request observations and retained price components."""

import json
import sqlite3

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, sequence, string
from hive.pricing import PricedUsage
from hive.usage import tokens

VIEW = """CREATE VIEW request_detail AS
WITH tiers(tier) AS (VALUES ('standard'),('fast'),('batch'),('flex')),
base AS (
 SELECT r.*, t.tier AS selected_tier,
 CASE WHEN r.host='claude' THEN r.model ELSE m.model END AS priced_model,
 m.conflicted,
 CASE WHEN m.conflicted=1 OR EXISTS (SELECT 1 FROM json_each(r.flags) WHERE value='unsupported_iteration')
 THEN NULL ELSE e.quote END AS quote,
 a.agent_type, a.agent AS metadata_agent, a.parent_agent, ev.response AS event_response, ev.query_source,
 ev.plugin, ev.mcp_tool, ev.effort, ev.host_usd,
 CASE WHEN ev.response IS NOT NULL AND (ev.task<>r.task OR ev.model<>r.model OR ev.input<>r.input-r.cached-r.cache_write-r.cache_write_1h
 OR ev.cached<>r.cached OR ev.writes<>r.cache_write+r.cache_write_1h OR ev.output<>r.output) THEN 1 ELSE 0 END AS join_mismatch
 FROM responses r
 JOIN tiers t ON r.host='codex' OR t.tier='standard'
 LEFT JOIN claude_request_events ev ON r.host='claude' AND r.response=ev.request_id
 LEFT JOIN turn_models m ON r.host='codex' AND r.task=m.task AND r.turn=m.turn AND COALESCE(r.agent,'')=m.agent
 LEFT JOIN claude_agent_parents a ON r.host='claude' AND r.task=a.task AND r.agent=a.agent
 LEFT JOIN response_estimates e ON r.response=e.response
 AND e.tier=CASE WHEN r.host='claude' THEN r.modifier_key ELSE t.tier END
)
SELECT host, task AS thread, response, CASE WHEN event_response IS NULL THEN 'transcript' ELSE 'both' END AS source, observed AS observed_at,
 CASE WHEN host='claude' THEN turn END AS prompt_id,
 CASE WHEN host='codex' THEN turn END AS turn,
 agent, agent_type, CASE WHEN host='claude' AND agent IS NOT NULL THEN CASE WHEN metadata_agent IS NULL THEN 'unknown' ELSE parent_agent END END AS parent_agent, query_source, skill,
 plugin, mcp_tool, priced_model AS model,
 CASE WHEN host='claude' THEN modifier_key ELSE selected_tier END AS modifier_key,
 CASE WHEN host='codex' THEN selected_tier END AS tier,
 effort,
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
 json_extract(quote,'$.usd') AS usd, host_usd AS host_cost_usd,
 CASE WHEN join_mismatch=1 THEN json_insert(CASE WHEN complete=0 THEN json_insert(flags,'$[#]','possibly_partial') ELSE flags END,'$[#]','event_token_mismatch') WHEN complete=0 THEN json_insert(flags,'$[#]','possibly_partial') ELSE flags END AS flags,
 usage AS _usage, quote AS _quote, modifiers AS _modifiers, conflicted AS _conflicted
FROM base
UNION ALL
SELECT 'claude', ev.task, ev.response, 'events', ev.observed, ev.prompt, NULL,
 NULL, NULL, NULL, ev.query_source, ev.skill, ev.plugin, ev.mcp_tool,
 ev.model, ev.modifier_key, NULL, ev.effort,
 ev.input, ev.cached, json_extract(ev.usage,'$.cache_write_input_tokens'),
 json_extract(ev.usage,'$.cache_write_1h_input_tokens'), ev.output, NULL, NULL,
 json_extract(e.quote,'$.components_usd.input'), json_extract(e.quote,'$.components_usd.cache_read'),
 json_extract(e.quote,'$.components_usd.cache_write_5m'), json_extract(e.quote,'$.components_usd.cache_write_1h'),
 json_extract(e.quote,'$.components_usd.output'), json_extract(e.quote,'$.components_usd.server_tools'),
 json_extract(e.quote,'$.usd'), ev.host_usd, ev.flags,
 ev.usage, e.quote, ev.modifiers, NULL
FROM claude_request_events ev
LEFT JOIN response_estimates e ON ev.response=e.response AND ev.modifier_key=e.tier
WHERE NOT EXISTS (SELECT 1 FROM responses r WHERE r.response=ev.request_id AND r.host='claude')
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
            parsed = tokens(parse(string(usage, "retained usage")))
            connection.execute(
                "UPDATE response_estimates SET quote=? WHERE response=? AND tier=?",
                (
                    json.dumps(
                        PricedUsage.read(
                            parse(string(value, "retained price")), parsed
                        ).value()
                    ),
                    response,
                    tier,
                ),
            )
    connection.execute("DROP VIEW IF EXISTS request_detail")
    connection.execute(VIEW)
