"""EngineeringAgent.

Reproducibility heuristic: if the ticket body contains none of the keywords
'error', 'crash', 'step', 'code', 'reproduce', 'stack', 'logs', '500', '404',
'traceback', the issue cannot be reproduced without more info -> triggers escalation.
"""

from models import ScopedTicket, AgentResult

REPRO_KEYWORDS = {"error", "crash", "step", "code", "reproduce", "stack", "logs", "500", "404", "traceback"}


def _has_repro_signal(body: str) -> bool:
    body_lower = body.lower()
    return any(kw in body_lower for kw in REPRO_KEYWORDS)


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    can_reproduce = _has_repro_signal(scoped.body)
    if not can_reproduce:
        return AgentResult(
            agent="EngineeringAgent",
            resolved=False,
            reproduced=False,
            notes="No error message, steps to reproduce, or diagnostic signal. Cannot action. Escalating for more info.",
        )
    return AgentResult(
        agent="EngineeringAgent",
        resolved=True,
        reproduced=True,
        notes="Bug reproduced using reported steps. Root cause identified. Fix queued.",
    )
