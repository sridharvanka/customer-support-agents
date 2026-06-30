"""
Field-level access control for agents.

This is the privacy enforcement layer. Each agent is only permitted to see
the fields listed in its set. build_scoped_ticket() in pseudonymizer.py uses
this to strip disallowed fields before constructing a ScopedTicket.

user_id and email are NEVER in any agent's allowed set — they exist only in
the raw ticket and the identity map held by pseudonymizer.py.
"""

FIELD_ACCESS: dict[str, set[str]] = {
    "BillingAgent": {
        "subject",
        "body",
        "subscription_tier",
        "account_status",
        "recent_payments",
    },
    "AuthAgent": {
        "subject",
        "body",
        "account_status",
        "last_login_at",
        "last_login_country",
    },
    "EngineeringAgent": {
        "subject",
        "body",
    },
    "OutageAgent": {
        "subject",
        "body",
    },
    "PolicyAgent": {
        "subject",
        "body",
        "subscription_tier",
    },
    "FraudDetectionAgent": {
        "subject",
        "body",
        "subscription_tier",
        "account_status",
        "recent_payments",
        "last_login_at",
        "last_login_country",
    },
    "HumanEscalationAgent": {
        "subject",
        "body",
    },
}

# All account fields that exist on a RawTicket — used to verify nothing leaks
ALL_ACCOUNT_FIELDS = {
    "subscription_tier",
    "account_status",
    "recent_payments",
    "last_login_at",
    "last_login_country",
}

# Fields that must NEVER appear in any agent payload
PII_FIELDS = {"user_id", "email"}
