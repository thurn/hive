"""Read-only descendant progress; parent status and direct accounting stay separate."""

import re
import sqlite3

from hive.dashboard_states import FAILURES, RUNNING
from hive.dashboard_values import rows
from hive.jsonvalue import parse, record, sequence, string


def category(value: dict[str, object]) -> str:
    """Explicit work kind wins; retain unknown work instead of inventing progress."""
    metadata = record(value.get("metadata") or {})
    kind = metadata.get("hive_work_kind")
    if kind in ("implementation", "review", "other"):
        return str(kind)
    task = str(metadata.get("hearts_task", ""))
    title = str(value.get("title", ""))
    if (
        value.get("issue_type") == "epic"
        or task in {"AUTHORIZE", "ACCEPT"}
        or re.match(r"^(?:Authorize|Approve|Accept)\b", title, re.IGNORECASE)
    ):
        return "other"
    if re.fullmatch(r"RV?\d+", task) or re.match(
        r"^(?:Review|Introspect)\b", title, re.IGNORECASE
    ):
        return "review"
    if re.fullmatch(r"H\d+", task) or metadata.get("implementation_authorized") is True:
        return "implementation" if value.get("issue_type") != "epic" else "other"
    return "other"


def progress(
    connection: sqlite3.Connection, bead: str, cards: list[dict[str, object]]
) -> dict[str, object] | None:
    cached = rows(
        connection,
        "SELECT b.bead,b.payload,b.refreshed,s.listed,r.deleted,r.renamed_to FROM bead_rows b LEFT JOIN bead_snapshots s ON s.bead=b.bead LEFT JOIN bead_replays r ON r.bead=b.bead",
    )
    retired = {
        str(row["bead"]) for row in cached if row["listed"] == 0 or row["deleted"]
    }
    aliases = {
        str(row["bead"]): str(row["renamed_to"]) for row in cached if row["renamed_to"]
    }
    records = {
        string(row["bead"], "bead"): record(parse(string(row["payload"], "bead")))
        for row in cached
    }
    if records.get(bead, {}).get("issue_type") != "epic":
        return None
    children: dict[str, set[str]] = {}
    blockers: dict[str, list[str]] = {}
    for identity, value in records.items():
        if identity in aliases:
            continue
        for raw in sequence(value.get("dependencies", []), "dependencies"):
            edge = record(raw)
            target = edge.get("depends_on_id", edge.get("id"))
            if not isinstance(target, str):
                continue
            relation = edge.get("dependency_type", edge.get("type", "blocks"))
            if relation == "parent-child":
                children.setdefault(aliases.get(target, target), set()).add(identity)
            elif relation == "blocks":
                prerequisite = records.get(target, {})
                metadata = record(prerequisite.get("metadata") or {})
                if (
                    prerequisite.get("status") != "closed"
                    or metadata.get("hive_resolution") != "completed"
                ):
                    blockers.setdefault(identity, []).append(target)
    seen = {bead}
    pending = list(children.get(bead, set()))
    while pending:
        child = pending.pop()
        if child not in seen:
            seen.add(child)
            pending.extend(children.get(child, set()))
    descendants = seen - {bead}
    by_bead = {str(card["bead"]): card for card in cards if card.get("bead")}
    roots = {
        string(row["thread"], "thread"): string(row["root"], "root")
        for row in rows(connection, "SELECT thread,root FROM codex_roots")
    }
    ci: dict[str, list[dict[str, object]]] = {}
    for item in rows(connection, "SELECT key,candidate,state,updated FROM card_ci"):
        ci.setdefault(string(item["key"], "key"), []).append(item)
    members = []
    owners: set[str] = set()
    total = unpriced = incomplete = missing = 0
    for identity in sorted(descendants):
        native = records[identity]
        card = by_bead.get(identity)
        attempts = ci.get("bead:" + identity, [])
        current = identity not in retired
        running = current and any(a["state"] in RUNNING for a in attempts)
        active = current and (native.get("status") == "in_progress" or running)
        assignee = native.get("assignee")
        owner = roots.get(assignee, assignee) if isinstance(assignee, str) else None
        if active and owner:
            owners.add(owner)
        metadata = record(native.get("metadata") or {})
        complete = (
            current
            and native.get("status") == "closed"
            and metadata.get("hive_resolution") == "completed"
        )
        if card:
            total += int(string(card["amount_picos"], "amount"))
            unpriced += int(str(card["unpriced"]))
            incomplete += int(bool(card["coverage"]))
        else:
            missing += 1
        members.append(
            dict[str, object](
                bead=identity,
                title=native.get("title"),
                category=category(native),
                direct_child=identity in children.get(bead, set()),
                current=current,
                native_status=native.get("status"),
                state=card.get("state") if card else "Unknown",
                completed=complete,
                cancelled=metadata.get("hive_resolution") == "cancelled",
                active=active,
                owner=owner,
                blockers=sorted(blockers.get(identity, [])),
                held=current and native.get("status") == "deferred",
                in_ci=running,
                ci_failed=any(a["state"] in FAILURES for a in attempts),
                candidates=attempts,
            )
        )
    from hive.dashboard_feed import freshness

    return dict[str, object](
        native_status=records[bead].get("status"),
        scope="All reachable descendants, each bead counted once; excludes this epic. Progress excludes deleted/missing records and renamed aliases; spend retains deleted work",
        classification="hive_work_kind, Hearts H/R/RV task codes and Review/Introspect prefixes; approval/acceptance/epics are other; remaining implementation_authorized work is implementation, otherwise other",
        groups=[
            dict[str, object](
                category=kind,
                total=sum(
                    m["category"] == kind and bool(m["current"]) for m in members
                ),
                completed=sum(
                    m["category"] == kind and bool(m["completed"]) for m in members
                ),
            )
            for kind in ("implementation", "review", "other")
        ],
        active=sum(bool(m["active"]) for m in members),
        in_ci=sum(bool(m["in_ci"]) for m in members),
        blocked=sum(
            bool(m["current"])
            and bool(m["blockers"])
            and m["native_status"] != "closed"
            for m in members
        ),
        held=sum(bool(m["held"]) for m in members),
        owners=sorted(owners),
        members=members,
        amount_picos=str(total),
        unpriced=unpriced,
        incomplete=incomplete,
        missing_costs=missing,
        refreshed=min(
            (
                string(row["refreshed"], "refreshed")
                for row in cached
                if row["bead"] in seen
            ),
            default=None,
        ),
        collector=freshness(connection),
    )
