"""AuthAgent — handles login failures, password reset, account access issues.

If the account is suspended and billing has NOT yet acted, defer.
If billing has acted (ran without anomaly), restore access.
If no suspension, resolve the auth issue directly.
"""

from models import ScopedTicket, AgentResult


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    account_status = scoped.account_status or "active"
    billing_result = state.get("BillingAgent")

    # Billing "acted" means it ran and did NOT flag an anomaly (anomaly goes to fraud, not here)
    billing_acted = billing_result is not None and not billing_result.anomaly_detected

    if account_status == "suspended" and not billing_acted:
        return AgentResult(
            agent="AuthAgent",
            resolved=False,
            notes="Account suspended. Auth access cannot be restored until billing resolves root cause.",
        )

    if account_status == "suspended" and billing_acted:
        return AgentResult(
            agent="AuthAgent",
            resolved=True,
            notes="Billing suspension resolved upstream. Account access restored. Customer advised to retry login.",
        )

    return AgentResult(
        agent="AuthAgent",
        resolved=True,
        notes="Auth issue investigated. Account active. Password reset link re-sent.",
    )
