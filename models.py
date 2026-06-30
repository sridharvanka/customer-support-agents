"""
Data models for the Customer Support Triage Pipeline.
All dataclasses used across the pipeline — defined once here, imported everywhere.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawTicket:
    """Full ticket as loaded from tickets.json. May contain PII — never passed to agents."""
    ticket_id: str
    created_at: str
    channel: str  # "email" | "chat" | "web_form"
    subject: str
    body: str
    account: dict  # full account blob including user_id, email, payment history, etc.


@dataclass
class ScopedTicket:
    """
    Pseudonymized, field-restricted ticket payload passed to each agent.
    PII has been removed. Only fields permitted by scope.py for this agent are populated.
    All other account fields are None.
    """
    trace_id: str           # opaque token — no PII
    agent_name: str         # which agent this payload was built for
    subject: str
    body: str
    # Account fields — present only if FIELD_ACCESS allows them for this agent
    subscription_tier: Optional[str] = None
    account_status: Optional[str] = None
    recent_payments: Optional[list] = None
    last_login_at: Optional[str] = None
    last_login_country: Optional[str] = None
    # user_id and email are NEVER present here


@dataclass
class AgentResult:
    """Structured output from a single agent. All agents return this schema."""
    agent: str              # agent name string e.g. "BillingAgent"
    resolved: bool          # True = ticket resolved, no further routing needed
    notes: str              # log-safe: decisions and reasoning only, never PII values

    # Optional fields — populated only when relevant to the agent
    anomaly_detected: bool = False          # BillingAgent only
    reproduced: Optional[bool] = None      # EngineeringAgent only
    risk_level: Optional[str] = None       # FraudDetectionAgent: "low" | "medium" | "high"
    action_taken: Optional[str] = None     # FraudDetectionAgent
    escalation_id: Optional[str] = None   # HumanEscalationAgent, OutageAgent
    policy_cited: Optional[str] = None    # PolicyAgent


@dataclass
class TicketTrace:
    """Full execution record for one ticket. Written to results.json."""
    trace_id: str
    ticket_id: str          # re-hydrated from identity map at output time only
    intents: list[str]
    confidence: float
    agents_invoked: list[str]
    results: list[AgentResult]
    resolution: str         # "resolved" | "escalated" | "fraud_flagged"
    steps: int
    notes: str              # accumulated agent notes, joined
