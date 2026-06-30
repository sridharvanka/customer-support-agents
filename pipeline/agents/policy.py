"""
PolicyAgent — handles questions about terms of service, refund policy, data requests.

Always resolves. Cites the relevant mock policy section.
"""

from models import ScopedTicket, AgentResult


POLICY_EXCERPTS = {
    "refund": (
        "Section 4.2 — Refunds: Pro and Enterprise subscribers are eligible for a prorated "
        "refund for unused days within 30 days of cancellation. Free tier accounts are not eligible."
    ),
    "data": (
        "Section 7.1 — Data Export: Customers may request a full export of their data at any time "
        "via Settings > Export. Requests are fulfilled within 48 hours."
    ),
    "cancellation": (
        "Section 4.1 — Cancellation: Subscriptions may be cancelled at any time. "
        "Access continues until end of the current billing period."
    ),
    "default": (
        "Section 1 — General Terms: Please refer to our full Terms of Service at example.com/terms. "
        "Our support team is happy to clarify any specific clause."
    ),
}


def _select_policy(body: str) -> tuple[str, str]:
    """Returns (policy_key, policy_text) based on keywords in the ticket body."""
    body_lower = body.lower()
    if any(w in body_lower for w in ["refund", "money back", "charge", "prorated"]):
        return "refund", POLICY_EXCERPTS["refund"]
    if any(w in body_lower for w in ["data", "export", "gdpr", "delete", "download"]):
        return "data", POLICY_EXCERPTS["data"]
    if any(w in body_lower for w in ["cancel", "cancellation", "end subscription"]):
        return "cancellation", POLICY_EXCERPTS["cancellation"]
    return "default", POLICY_EXCERPTS["default"]


def run(scoped: ScopedTicket, state: dict) -> AgentResult:
    policy_key, policy_text = _select_policy(scoped.body)
    tier = scoped.subscription_tier or "unknown"

    return AgentResult(
        agent="PolicyAgent",
        resolved=True,
        policy_cited=policy_key,
        notes=(
            f"Policy query resolved for {tier} tier account. "
            f"Cited: {policy_text}"
        ),
    )
