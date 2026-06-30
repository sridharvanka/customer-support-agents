"""
Customer Support Triage Pipeline — Entry point.

Usage:
    python main.py

Reads tickets.json, processes each ticket through the agent pipeline,
prints an execution trace to stdout, and writes results.json.

Requires ANTHROPIC_API_KEY in .env
"""

import json
import os
import sys
from dotenv import load_dotenv

from models import RawTicket, AgentResult, TicketTrace
from pipeline.pseudonymizer import pseudonymize, build_scoped_ticket
from pipeline.orchestrator import classify, build_chain, maybe_inject, is_terminal
from pipeline import agents

load_dotenv()

# Agent registry — maps agent name string to its run() function
AGENT_REGISTRY = {
    "BillingAgent":        agents.billing.run,
    "AuthAgent":           agents.auth.run,
    "EngineeringAgent":    agents.engineering.run,
    "OutageAgent":         agents.outage.run,
    "PolicyAgent":         agents.policy.run,
    "FraudDetectionAgent": agents.fraud.run,
    "HumanEscalationAgent": agents.escalation.run,
}

MAX_STEPS = 5  # Hard ceiling — no ticket may invoke more than 5 agents


def derive_resolution(results: list[AgentResult]) -> str:
    """Derives a single resolution label from the list of agent results."""
    for result in results:
        if result.agent == "FraudDetectionAgent":
            return "fraud_flagged"
    for result in results:
        if result.escalation_id:
            return "escalated"
    if any(r.resolved for r in results):
        return "resolved"
    return "escalated"


def process_ticket(raw: RawTicket) -> tuple[TicketTrace, dict]:
    """
    Runs a single ticket through the full pipeline.

    Returns:
        trace: TicketTrace — full execution record
        identity_map: dict — { trace_id: { user_id, email, ticket_id } } for re-hydration
    """
    # Step 1: Pseudonymize — strip PII, get opaque trace_id
    trace_id, identity_map = pseudonymize(raw)

    # Step 2: Classify intent(s) — the single LLM call
    print(f"  Classifying {raw.ticket_id}…", end=" ", flush=True)
    intents, confidence = classify(raw)
    print(f"intents={intents} confidence={confidence:.2f}")

    # Step 3: Build initial agent chain
    chain = build_chain(intents)

    # Step 4: Execute agents sequentially
    state: dict[str, AgentResult] = {}
    results: list[AgentResult] = []

    for _ in range(MAX_STEPS):
        if not chain:
            break

        agent_name = chain.pop(0)
        run_fn = AGENT_REGISTRY.get(agent_name)

        if run_fn is None:
            # BUILDER NOTE: Unknown agent name — skip rather than crash.
            # In production this would be a hard error.
            print(f"  WARNING: Unknown agent '{agent_name}' — skipping.")
            continue

        # Build scoped (field-restricted, pseudonymized) payload for this agent
        scoped = build_scoped_ticket(raw, trace_id, agent_name)

        # Run agent
        result = run_fn(scoped, state)
        state[agent_name] = result
        results.append(result)

        # Dynamic chain mutation based on result
        chain = maybe_inject(chain, result)

        # Exit conditions
        if is_terminal(agent_name):
            break
        if result.resolved:
            break

    # Step 5: Build trace
    resolution = derive_resolution(results)
    all_notes = " | ".join(r.notes for r in results if r.notes)

    trace = TicketTrace(
        trace_id=trace_id,
        ticket_id=raw.ticket_id,  # will be re-hydrated in tracer from identity_map
        intents=intents,
        confidence=confidence,
        agents_invoked=[r.agent for r in results],
        results=results,
        resolution=resolution,
        steps=len(results),
        notes=all_notes,
    )

    return trace, identity_map


def load_tickets(path: str = "tickets.json") -> list[RawTicket]:
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Run from the customer-support-agents directory.")
        sys.exit(1)

    with open(path) as f:
        raw_data = json.load(f)

    return [RawTicket(**t) for t in raw_data]


def main():
    from tracer import print_trace, write_results

    print("Customer Support Triage Pipeline")
    print("=" * 60)

    tickets = load_tickets()
    print(f"Loaded {len(tickets)} tickets\n")

    all_traces: list[TicketTrace] = []
    combined_identity_map: dict = {}

    for raw in tickets:
        trace, identity_map = process_ticket(raw)
        all_traces.append(trace)
        combined_identity_map.update(identity_map)

    # Output traces
    for trace in all_traces:
        print_trace(trace, combined_identity_map)

    # Write results.json
    write_results(all_traces, combined_identity_map)


if __name__ == "__main__":
    main()
