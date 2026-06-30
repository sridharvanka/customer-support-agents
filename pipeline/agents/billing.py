"""BillingAgent."""

from models import ScopedTicket, AgentResult


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    account_status = scoped.account_status or "unknown"
    payments = scoped.recent_payments or []
    has_failed = any(p.get("status") == "failed" for p in payments)
    is_suspended = account_status == "suspended"
    has_big_success = any(
        p.get("status") == "success" and p.get("amount", 0) > 100
        for p in payments
    )
    anomaly = is_suspended and has_failed and has_big_success
    if anomaly:
        return AgentResult(agent="BillingAgent", resolved=False, anomaly_detected=True,
            notes="Suspended account with high-value charge and failed payment. Flagging for fraud.")
    if is_suspended and has_failed:
        return AgentResult(agent="BillingAgent", resolved=False, anomaly_detected=False,
            notes="Failed payment on suspended account. Retry initiated. Auth team to restore access.")
    if has_failed:
        return AgentResult(agent="BillingAgent", resolved=True, anomaly_detected=False,
            notes="Failed payment on active account. Retry initiated. Customer to verify card.")
    return AgentResult(agent="BillingAgent", resolved=True, anomaly_detected=False,
        notes="Billing inquiry reviewed. No anomalies. Resolved per standard policy.")
