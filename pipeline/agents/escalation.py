"""
HumanEscalationAgent — terminal agent for all unresolved or ambiguous tickets.

Triggered by:
  - Low classifier confidence (< 0.7)
  - EngineeringAgent unable to reproduce bug
  - Outage detection (via OutageAgent short-circuit)
  - Direct escalation intent

Generates a handoff summary from all prior agent notes.
Always terminal.
"""

import uuid
from models import ScopedTicket, AgentResult


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    escalation_id = f"ESC-{str(uuid.uuid4())[:8].upper()}"

    # Collect prior agent notes for handoff context
    prior_notes = []
    for agent_name, result in state.items():
        if result.notes:
            prior_notes.append(f"[{agent_name}] {result.notes}")

    handoff_summary = (
        f"Escalation {escalation_id} created. "
        + (
            "Prior agent context: " + " | ".join(prior_notes)
            if prior_notes
            else "No prior agent context — ticket routed directly to human review."
        )
    )

    return AgentResult(
        agent="HumanEscalationAgent",
        resolved=False,
        escalation_id=escalation_id,
        notes=handoff_summary,
    )
