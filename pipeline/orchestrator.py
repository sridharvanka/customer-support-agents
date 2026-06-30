"""
Orchestrator — the brain of the pipeline.

Responsibilities:
  1. classify()     — LLM call to determine ticket intents (real Anthropic API)
  2. build_chain()  — maps intents to ordered agent list
  3. maybe_inject() — dynamically mutates chain after each agent result

Only one LLM call happens in this entire pipeline: inside classify().
All agents are deterministic functions.
"""

import json
import os
import anthropic
from models import RawTicket, AgentResult

# ---------------------------------------------------------------------------
# Intent classification — the single LLM call in the pipeline
# ---------------------------------------------------------------------------

_client = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


CLASSIFY_TOOL = {
    "name": "classify_ticket",
    "description": "Classify a support ticket into one or more intents.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intents": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["billing", "auth", "product_bug", "outage", "policy"]
                },
                "description": "All intents present in the ticket. Can be multiple.",
                "minItems": 1
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in the classification, 0.0 to 1.0.",
                "minimum": 0.0,
                "maximum": 1.0
            }
        },
        "required": ["intents", "confidence"]
    }
}

SYSTEM_PROMPT = """You are a support ticket classifier for a SaaS product.

Classify tickets into one or more of these intents:
- billing: charges, refunds, invoices, subscription changes, payment failures
- auth: login failures, password reset, SSO, MFA, account access issues (not caused by billing suspension)
- product_bug: a specific feature or flow not working as expected (user-specific issue)
- outage: platform-wide failure, 503 errors, multiple users affected, service completely down
- policy: questions about terms of service, refund policies, data requests, compliance

Important boundary rules:
- If account is suspended due to non-payment → billing (auth failure is a symptom)
- If issue affects only this user → product_bug; if widespread → outage
- A ticket can have multiple intents (e.g. billing + auth)

Return low confidence (< 0.7) only if the ticket is genuinely ambiguous with no clear signal."""


def classify(raw: RawTicket) -> tuple[list[str], float]:
    """
    Makes a single Anthropic API call to classify the ticket's intent(s).

    Returns:
        intents: list of intent strings e.g. ["billing", "auth"]
        confidence: float 0.0–1.0

    If confidence < 0.7, returns (["escalate"], confidence) to trigger
    immediate HumanEscalation without routing to any specialist agent.
    """
    client = _get_client()

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # Fast + cheap for classification
        max_tokens=256,
        system=SYSTEM_PROMPT,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=[
            {
                "role": "user",
                "content": f"Subject: {raw.subject}\n\nBody: {raw.body}"
            }
        ]
    )

    # Extract tool use result
    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"),
        None
    )
    if tool_use_block is None:
        # Fallback: if tool use didn't fire, escalate
        return ["escalate"], 0.0

    result = tool_use_block.input
    intents = result.get("intents", ["escalate"])
    confidence = result.get("confidence", 0.0)

    if confidence < 0.7:
        return ["escalate"], confidence

    return intents, confidence


# ---------------------------------------------------------------------------
# Chain building — deterministic routing logic
# ---------------------------------------------------------------------------

# Priority order when multiple intents are present
INTENT_PRIORITY = ["outage", "billing", "auth", "product_bug", "policy"]

INTENT_TO_AGENT = {
    "billing": "BillingAgent",
    "auth": "AuthAgent",
    "product_bug": "EngineeringAgent",
    "outage": "OutageAgent",
    "policy": "PolicyAgent",
    "escalate": "HumanEscalationAgent",
}

# Agents that are always terminal — loop exits immediately after these
TERMINAL_AGENTS = {"HumanEscalationAgent", "OutageAgent", "FraudDetectionAgent"}


def build_chain(intents: list[str]) -> list[str]:
    """
    Maps a list of intents to an ordered list of agent names.

    Outage short-circuits everything — if present, only HumanEscalationAgent runs.
    Multiple intents are ordered by INTENT_PRIORITY.
    """
    if "outage" in intents or "escalate" in intents:
        return ["HumanEscalationAgent"]

    # Sort by priority, deduplicate, map to agent names
    ordered = sorted(intents, key=lambda i: INTENT_PRIORITY.index(i) if i in INTENT_PRIORITY else 99)
    return [INTENT_TO_AGENT[intent] for intent in ordered if intent in INTENT_TO_AGENT]


def maybe_inject(chain: list[str], last_result: AgentResult) -> list[str]:
    """
    Mutates and returns the chain after each agent step based on the agent's result.

    Rules:
      - BillingAgent returned anomaly_detected=True → insert FraudDetectionAgent next
      - EngineeringAgent returned reproduced=False → replace rest of chain with HumanEscalationAgent
    """
    if last_result.agent == "BillingAgent" and last_result.anomaly_detected:
        # Inject FraudDetectionAgent immediately after current position
        # Chain has already advanced past BillingAgent, so prepend to remaining
        if "FraudDetectionAgent" not in chain:
            chain.insert(0, "FraudDetectionAgent")

    if last_result.agent == "EngineeringAgent" and last_result.reproduced is False:
        # Replace everything remaining with human escalation
        chain.clear()
        chain.append("HumanEscalationAgent")

    return chain


def is_terminal(agent_name: str) -> bool:
    """Returns True if the agent is always terminal — pipeline exits after it."""
    return agent_name in TERMINAL_AGENTS
