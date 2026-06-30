"""
Pseudonymization layer.

This is the only module that ever holds the mapping between trace_ids and real
identifiers. The pipeline never sees real user_id or email — only trace_id.
Re-hydration (trace_id → real identifiers) is performed only at output time by tracer.py.
"""

import uuid
from models import RawTicket, ScopedTicket
from pipeline.scope import FIELD_ACCESS, PII_FIELDS


def pseudonymize(raw: RawTicket) -> tuple[str, dict]:
    """
    Assigns an opaque trace_id to a ticket and builds the identity map entry.

    Returns:
        trace_id: a uuid4 string — used by all agents instead of real identifiers
        identity_map: { trace_id: { "user_id": ..., "email": ..., "ticket_id": ... } }
                      kept outside the pipeline; passed only to tracer.py
    """
    trace_id = str(uuid.uuid4())
    identity_map = {
        trace_id: {
            "user_id": raw.account.get("user_id"),
            "email": raw.account.get("email"),
            "ticket_id": raw.ticket_id,
        }
    }
    return trace_id, identity_map


def build_scoped_ticket(raw: RawTicket, trace_id: str, agent_name: str) -> ScopedTicket:
    """
    Constructs a ScopedTicket for the named agent, applying FIELD_ACCESS restrictions.

    Only fields in FIELD_ACCESS[agent_name] are populated.
    PII fields (user_id, email) are never included regardless of the access map.
    Fields not in the agent's allowed set are set to None.
    """
    allowed = FIELD_ACCESS.get(agent_name, set())
    account = raw.account

    # Defensive check: ensure no PII sneaks through
    for pii_field in PII_FIELDS:
        if pii_field in allowed:
            raise ValueError(
                f"SECURITY: {pii_field} must never appear in any agent's FIELD_ACCESS set."
            )

    return ScopedTicket(
        trace_id=trace_id,
        agent_name=agent_name,
        subject=raw.subject,
        body=raw.body,
        subscription_tier=account.get("subscription_tier") if "subscription_tier" in allowed else None,
        account_status=account.get("account_status") if "account_status" in allowed else None,
        recent_payments=account.get("recent_payments") if "recent_payments" in allowed else None,
        last_login_at=account.get("last_login_at") if "last_login_at" in allowed else None,
        last_login_country=account.get("last_login_country") if "last_login_country" in allowed else None,
    )
