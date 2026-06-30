"""
FraudDetectionAgent — dynamically injected when BillingAgent flags an anomaly.

Assesses risk based on login location mismatch and payment pattern.
Always terminal — does not chain to further agents.

Risk levels:
  high   — suspended account + failed payment + login from unexpected country
  medium — suspended account + failed payment (no location anomaly)
  low    — anomaly flag set but no corroborating signals (conservative)
"""

from models import ScopedTicket, AgentResult

# Countries considered "expected" for this fictional product's user base.
# In production this would be per-account baseline, not a global list.
LOW_RISK_COUNTRIES = {"US", "CA", "UK", "AU", "DE", "FR", "NL", "SE", "NZ"}


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    payments = scoped.recent_payments or []
    login_country = scoped.last_login_country or "US"
    account_status = scoped.account_status or "active"

    has_failed_payment = any(p.get("status") == "failed" for p in payments)
    is_suspended = account_status == "suspended"
    location_anomaly = login_country not in LOW_RISK_COUNTRIES

    if is_suspended and has_failed_payment and location_anomaly:
        risk_level = "high"
        action = (
            f"Account flagged HIGH RISK. Suspended account with failed payment and login from "
            f"unexpected region detected. Account locked pending manual review. "
            f"Customer will be contacted via verified email channel."
        )
    elif is_suspended and has_failed_payment:
        risk_level = "medium"
        action = (
            "Account flagged MEDIUM RISK. Suspended account with failed payment pattern. "
            "Additional verification required before account reinstatement. "
            "Customer prompted to complete identity verification."
        )
    else:
        risk_level = "low"
        action = (
            "Anomaly reviewed. Risk assessed as LOW. "
            "No corroborating fraud signals found. Billing team to proceed with standard resolution."
        )

    return AgentResult(
        agent="FraudDetectionAgent",
        resolved=False,  # Fraud cases always require human review — never auto-resolved
        risk_level=risk_level,
        action_taken=action,
        notes=f"Fraud review complete. Risk level: {risk_level}. {action}",
    )
