"""Deterministic, dollar-ranked fleet observations; no generated advice."""

import sqlite3
from collections import Counter

from hive.dashboard_values import rows
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.pricing import dollars
from hive.tollgate_observation import candidates

REVIEW_PERCENT = 35
UNOWNED_PERCENT = 20
COVERAGE_PERCENT = 10
REWRITE_MINIMUM: int = 10**12
CI_FAILURES = 3
STALL_SECONDS = 7200


def hotspots(
    connection: sqlite3.Connection,
    cards: list[dict[str, object]],
    summary: dict[str, object],
    requests: list[dict[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    total = int(string(summary["amount_picos"], "total"))
    start = string(summary["start"], "window start")
    roles = {
        string(record(v)["role"], "role"): int(
            string(record(v)["amount_picos"], "amount")
        )
        for v in sequence(summary["roles"], "roles")
    }
    by_key: Counter[str] = Counter()
    by_thread: Counter[str] = Counter()
    review_keys: set[str] = set()
    for value in requests:
        amount = int(string(value["amount_picos"] or "0", "amount"))
        by_thread[string(value["thread"], "thread")] += amount
        for raw in sequence(value["shares"], "shares"):
            share = record(raw)
            key = string(share["key"], "key")
            by_key[key] += int(string(share["amount_picos"], "amount"))
            if value["role"] == "warden":
                review_keys.add(key)

    def add(kind: str, title: str, amount: int, keys: list[str]) -> None:
        result.append(
            record(
                dict[str, object](
                    kind=kind,
                    title=title,
                    amount_picos=str(amount),
                    usd=dollars(amount),
                    keys=sorted(set(keys)),
                )
            )
        )

    review = roles.get("warden", 0)
    if review * 100 > roles.get("executor", 0) * REVIEW_PERCENT:
        add(
            "review_share",
            "Review exceeds 35% of executor spend",
            review,
            sorted(review_keys),
        )
    kinds = {string(v["key"], "key"): v["kind"] for v in cards}
    tails = {k: a for k, a in by_key.items() if kinds.get(k) in {"tail", "small_tails"}}
    if sum(tails.values()) * 100 > total * UNOWNED_PERCENT:
        add(
            "unowned",
            "Unowned tails exceed 20% of spend; claim earlier",
            sum(tails.values()),
            list(tails),
        )
    incomplete = int(string(summary["incomplete_picos"], "incomplete"))
    if incomplete * 100 > total * COVERAGE_PERCENT:
        keys = [
            string(record(s)["key"], "key")
            for r in requests
            if r["coverage"]
            for s in sequence(r["shares"], "shares")
        ]
        add(
            "coverage",
            "More than 10% of spend has incomplete coverage",
            incomplete,
            keys,
        )
    for card in cards:
        key = string(card["key"], "key")
        if (
            card["state"] == "Stalled"
            and integer(card["idle_seconds"], "idle") > STALL_SECONDS
            and key in by_key
        ):
            add("stall", "Work has been idle for over two hours", by_key[key], [key])
    for snapshot in rows(
        connection, "SELECT thread,payload FROM dashboard_diagnostic_sets"
    ):
        thread = string(snapshot["thread"], "thread")
        if thread not in by_thread:
            continue
        for raw in sequence(
            record(parse(string(snapshot["payload"], "snapshot")))["insights"],
            "insights",
        ):
            insight = record(raw)
            if str(insight["at"]) < start:
                continue
            keys = [string(k, "key") for k in sequence(insight["keys"], "keys")]
            if insight["kind"] == "cache_rewrite":
                amount = int(string(insight["amount_picos"], "amount"))
                if amount > REWRITE_MINIMUM:
                    add("cache_rewrite", "Idle cache rewrite exceeded $1", amount, keys)
            else:
                amount = sum(
                    int(string(r["amount_picos"] or "0", "amount"))
                    for r in requests
                    if r["thread"] == thread
                    and (r["agent"] or "") == insight["agent"]
                    and str(insight["at"])
                    <= str(r["observed"])
                    <= str(insight["until"])
                )
                add("retry_loop", "Repeated identical tool failures", amount, keys)
    failures: Counter[tuple[str, str]] = Counter()
    for candidate in candidates(connection):
        keys = {
            "bead:" + string(b, "bead")
            for raw in sequence(candidate["links"], "links")
            for b in sequence(record(raw)["beads"], "beads")
        }
        for raw in sequence(candidate["attempts"], "attempts"):
            attempt = record(raw)
            if str(attempt.get("finished_at") or "") < start:
                continue
            for step_raw in sequence(attempt["steps"], "steps"):
                step = record(step_raw)
                if step["exit_code"] not in (None, 0) or step["result_class"] in {
                    "failed",
                    "failure",
                    "timed-out",
                }:
                    for key in keys:
                        failures[key, string(step["name"], "step")] += 1
    for (key, step), count in failures.items():
        if count >= CI_FAILURES and key in by_key:
            add("ci_churn", f"{count} failed CI attempts on {step}", by_key[key], [key])
    return sorted(
        result,
        key=lambda v: (
            -int(string(v["amount_picos"], "amount")),
            string(v["kind"], "kind"),
            string(v["title"], "title"),
        ),
    )
