"""Exact agent and skill partitions share the report's retained price evidence."""

import sqlite3
from dataclasses import dataclass

from hive.identity import ThreadId
from hive.jsonvalue import sequence, string
from hive.pricing import dollars
from hive.usage_store import row


@dataclass(frozen=True)
class Subtotal:
    responses: int = 0
    priced: int = 0
    amount: int = 0

    def add(self, amount: int | None) -> "Subtotal":
        return Subtotal(
            self.responses + 1,
            self.priced + int(amount is not None),
            self.amount + (amount or 0),
        )

    def value(self) -> dict[str, object]:
        return {
            "responses": self.responses,
            "priced_responses": self.priced,
            "unpriced_responses": self.responses - self.priced,
            "usd": dollars(self.amount) if self.priced else None,
        }


def report(
    connection: sqlite3.Connection,
    task: ThreadId,
    agents: dict[str | None, Subtotal],
    skills: dict[str | None, Subtotal],
    events: dict[str, object],
) -> dict[str, object]:
    fetched: object = connection.execute(
        "SELECT agent,agent_type,description,tool_use_id,parent_agent,spawn_depth "
        "FROM claude_agent_parents WHERE task=?",
        (task,),
    ).fetchall()
    metadata: dict[str, dict[str, object]] = {}
    for raw in sequence(fetched, "agent metadata"):
        agent, kind, description, tool, parent, depth = row(raw, 6)
        metadata[string(agent, "agent ID")] = {
            "agent_type": kind,
            "description": description,
            "spawned_by_tool_use": tool,
            "parent_agent": parent,
            "spawn_depth": depth,
        }
    side: dict[str, object] = {
        "label": "event_only",
        "responses": events.get("event_only_requests", 0),
        "usd": events.get("event_only_usd"),
        "unpriced_responses": events.get("event_only_unpriced_requests", 0),
        "by_query_source": events.get("event_only_by_query_source", []),
    }
    return {
        "by_agent": [
            {
                "agent": agent,
                "label": "main" if agent is None else "subagent",
                **(
                    {"parent_agent": None}
                    if agent is None
                    else metadata.get(agent, {"parent_agent": "unknown"})
                ),
                **subtotal.value(),
            }
            for agent, subtotal in sorted(
                agents.items(), key=lambda item: item[0] or ""
            )
        ]
        + [side],
        "by_skill": [
            {"skill": skill, **subtotal.value()}
            for skill, subtotal in sorted(
                skills.items(), key=lambda item: item[0] or ""
            )
        ]
        + [side],
    }
