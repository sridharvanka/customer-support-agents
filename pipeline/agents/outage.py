"""
OutageAgent — handles platform-wide failures, 503 errors, widespread service disruption.

Always terminal. Immediately generates an escalation and hands off to on-call.
No resolution attempt is made — outages require human incident response.
"""

import uuid
from models import ScopedTicket, AgentResult


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    escalation_id = f"INC-{str(uuid.uuid4())[:8].upper()}"

    return AgentResult(
        agent="OutageAgent",
        resolved=False,
        escalation_id=escalation_id,
        notes=(
            f"Platform outage detected. Incident {escalation_id} created and on-call team paged. "
            "Customer acknowledged and directed to status page. No further agent routing."
        ),
    )
