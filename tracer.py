"""
Tracer — formats and outputs execution traces.

print_trace() writes a human-readable decision path to stdout.
write_results() serialises all TicketTrace objects to results.json.

Neither function ever receives or prints PII — only trace_ids and structural data.
ticket_id is re-hydrated from the identity map here, at the boundary between
the pipeline and human-readable output.
"""

import json
from dataclasses import asdict
from models import TicketTrace

# ANSI colour codes for CLI readability
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

RESOLUTION_COLOURS = {
    "resolved":      GREEN,
    "escalated":     YELLOW,
    "fraud_flagged": RED,
}


def _resolution_colour(resolution: str) -> str:
    return RESOLUTION_COLOURS.get(resolution, RESET)


def print_trace(trace: TicketTrace, identity_map: dict) -> None:
    """
    Pretty-prints the execution trace for one ticket to stdout.
    Re-hydrates ticket_id from identity_map for display only.
    """
    identity = identity_map.get(trace.trace_id, {})
    display_id = identity.get("ticket_id", trace.trace_id)

    colour = _resolution_colour(trace.resolution)

    print(f"\n{'─' * 60}")
    print(f"{BOLD}Ticket:{RESET} {display_id}  {DIM}(trace: {trace.trace_id[:8]}…){RESET}")
    print(f"{BOLD}Intents:{RESET} {', '.join(trace.intents)}  {DIM}(confidence: {trace.confidence:.2f}){RESET}")
    print(f"{BOLD}Path:{RESET}   ", end="")

    # Print agent chain with arrows
    for i, agent in enumerate(trace.agents_invoked):
        is_last = i == len(trace.agents_invoked) - 1
        print(f"{CYAN}{agent}{RESET}", end="")
        if not is_last:
            print(f" → ", end="")
    print()

    # Per-agent decision notes
    for result in trace.results:
        icon = "✓" if result.resolved else "→"
        extras = []
        if result.anomaly_detected:
            extras.append(f"{RED}anomaly_detected{RESET}")
        if result.reproduced is not None:
            extras.append(f"reproduced={result.reproduced}")
        if result.risk_level:
            extras.append(f"risk={result.risk_level}")
        if result.escalation_id:
            extras.append(f"id={result.escalation_id}")
        if result.policy_cited:
            extras.append(f"policy={result.policy_cited}")
        extra_str = f"  {DIM}[{', '.join(extras)}]{RESET}" if extras else ""
        print(f"  {icon} {result.agent}{extra_str}")
        print(f"    {DIM}{result.notes[:120]}{'…' if len(result.notes) > 120 else ''}{RESET}")

    print(f"{BOLD}Resolution:{RESET} {colour}{trace.resolution.upper()}{RESET}  {DIM}({trace.steps} step{'s' if trace.steps != 1 else ''}){RESET}")


def _serialise_trace(trace: TicketTrace, identity_map: dict) -> dict:
    """Converts a TicketTrace to a JSON-serialisable dict, re-hydrating ticket_id."""
    d = asdict(trace)
    identity = identity_map.get(trace.trace_id, {})
    d["ticket_id"] = identity.get("ticket_id", trace.trace_id)
    return d


def write_results(traces: list[TicketTrace], identity_map: dict, path: str = "results.json") -> None:
    """Serialises all traces to results.json. Overwrites on each run."""
    output = {
        "total_tickets": len(traces),
        "summary": {
            "resolved":      sum(1 for t in traces if t.resolution == "resolved"),
            "escalated":     sum(1 for t in traces if t.resolution == "escalated"),
            "fraud_flagged": sum(1 for t in traces if t.resolution == "fraud_flagged"),
        },
        "tickets": [_serialise_trace(t, identity_map) for t in traces],
    }

    with open(path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{BOLD}Results written to {path}{RESET}")
    print(f"  Resolved:      {output['summary']['resolved']}")
    print(f"  Escalated:     {output['summary']['escalated']}")
    print(f"  Fraud flagged: {output['summary']['fraud_flagged']}")
