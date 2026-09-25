"""Public event coverage presentation over immutable request evidence."""

from collections import Counter

from hive.event_evidence import EventEvidence
from hive.pricing import dollars


def report(data: EventEvidence) -> dict[str, object]:
    counts = Counter(dict(data.counts))
    versions, groups = dict(data.versions), dict(data.groups)
    assumptions = data.assumptions
    side_amount, host_micros = data.side_amount, data.host_micros
    gaps, sequence_conflicts = data.gaps, data.sequence_conflicts
    failures, listener = data.failures, data.listener
    coverage, conditions = data.coverage, data.conditions
    host_matches, amount = data.host_matches, data.amount
    return {
        "event_coverage": coverage,
        "event_sequence_gaps": gaps,
        "conflicting_event_sequences": sequence_conflicts,
        "event_only_requests": counts["only"],
        "event_only_usd": dollars(side_amount),
        "event_only_unpriced_requests": counts["only"] - counts["priced_only"],
        "event_only_by_query_source": [
            {
                "query_source": label,
                "requests": total,
                "priced_requests": valued,
                "usd": dollars(subtotal),
            }
            for label, (total, valued, subtotal) in sorted(groups.items())
        ],
        "unjoinable_events": counts["unjoinable"],
        "listener_rejections": listener,
        "rejected_records": failures,
        "api_errors": data.api_errors,
        "event_token_mismatches": counts["mismatch"],
        "join_versions": [
            {
                "version": name,
                "requests": total,
                "matched": joined,
                "join_failure": joined == 0,
            }
            for name, (total, joined) in sorted(versions.items())
        ],
        "event_host_total_usd": dollars(host_micros * 1_000_000),
        "event_host_total_matches": host_matches,
        "complete_estimate_usd": (
            dollars(amount + side_amount) if not conditions else None
        ),
        "complete_estimate_reasons": list(conditions),
        "event_assumptions": sorted(assumptions),
    }
