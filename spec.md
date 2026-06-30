# Customer Support Triage Pipeline — Spec

*Status: Ready to build · Pattern: Orchestrator-workers workflow (per Anthropic's building effective agents guidance)*

---

## What we're building

A demo agentic workflow that reads a JSON array of synthetic support tickets and routes each one through a chain of specialist agents, determined dynamically at runtime. The same input structure (a ticket) produces radically different agent paths — that contrast is the demo's core value.

**Audience:** Recruiters and hiring managers. Demonstrates agentic workflow design, privacy-aware architecture, and production-ready thinking — not just working code.

---

## Architecture pattern

This is a **workflow**, not a fully autonomous agent (per Anthropic's distinction). The routing logic lives in deterministic code; LLM calls are made at specific, deliberate steps. We use direct API calls — no framework overhead.

Pattern: **Orchestrator-workers** with dynamic chain injection.

```
tickets.json
    │
    ▼
[Orchestrator] ── classifies intent(s) via LLM structured output
    │
    ├──▶ BillingAgent
    │       └── if anomaly_detected → inject FraudDetectionAgent
    ├──▶ EngineeringAgent
    │       └── if cannot_reproduce → swap to HumanEscalationAgent
    ├──▶ PolicyAgent
    └──▶ HumanEscalationAgent (terminal)
```

Multi-intent tickets (e.g. payment failed + can't log in) route to multiple agents. The orchestrator re-evaluates the chain after each agent result.

---

## Agents

| Agent | Triggers on | Key output fields | Terminal? |
|---|---|---|---|
| BillingAgent | `billing` intent | `resolved`, `anomaly_detected`, `notes` | If resolved |
| AuthAgent | `auth` intent | `reproduced`, `resolved`, `notes` | If resolved |
| EngineeringAgent | `product_bug` intent | `reproduced`, `resolved`, `notes` | If resolved |
| OutageAgent | `outage` intent | `escalation_id` | Always (short-circuit) |
| PolicyAgent | `policy` intent | `policy_cited`, `resolved`, `notes` | If resolved |
| FraudDetectionAgent | BillingAgent `anomaly_detected=true` | `risk_level`, `action_taken` | Always |
| HumanEscalationAgent | Low confidence / EngineeringAgent `reproduced=false` / OutageAgent | `escalation_id` | Always |

---

## Ticket schema

### What enters the pipeline (raw intake)

```json
{
  "ticket_id": "TKT-001",
  "created_at": "2025-06-30T10:00:00Z",
  "channel": "email | chat | web_form",
  "subject": "string",
  "body": "string",
  "account": {
    "user_id": "USR-123",
    "email": "user@example.com",
    "subscription_tier": "pro | free | enterprise",
    "account_status": "active | suspended | churned",
    "recent_payments": [{ "date": "...", "amount": 0, "status": "success | failed" }],
    "last_login_at": "ISO8601",
    "last_login_country": "US"
  }
}
```

### What agents see (pseudonymized, field-level access)

PII is stripped at intake before any agent call. Each agent receives only the fields it needs.

| Field | BillingAgent | EngineeringAgent | PolicyAgent | FraudDetectionAgent |
|---|---|---|---|---|
| trace_id | ✓ | ✓ | ✓ | ✓ |
| subject + body | ✓ | ✓ | ✓ | ✓ |
| subscription_tier | ✓ | | ✓ | ✓ |
| account_status | ✓ | | | ✓ |
| recent_payments | ✓ | | | ✓ |
| last_login_at | | | | ✓ |
| last_login_country | | | | ✓ |
| user_id / email | ✗ | ✗ | ✗ | ✗ |

---

## Privacy design

**Context:** Synthetic data only for this demo. Design reflects production-ready thinking for a portfolio audience.

### Pseudonymization at intake

Before the ticket enters the agent pipeline, a `pseudonymize()` function:
- Replaces `user_id`, `email`, `name`, and `ip_address` with an opaque `trace_id`
- Stores the mapping in an in-memory lookup table (`{ trace_id → real identifiers }`)
- Agents never see real identifiers — they work with `trace_id` only
- Re-hydration (trace_id → real data) happens only at final output, if needed for human handoff

### Field-level access per agent

Each agent receives a scoped payload — not the full ticket. The orchestrator builds the payload per agent using a field access map (see table above). This enforces data minimization: EngineeringAgent has no reason to see payment history; PolicyAgent has no reason to see login location.

### Logging discipline

Execution traces log structure and decisions, not content:
- ✓ `BillingAgent: anomaly_detected=true, routing to FraudDetectionAgent`
- ✗ `BillingAgent: payment failure on account user@example.com`

### Why this matters (production relevance)

- **GDPR / CCPA:** Lawful basis is required for LLM API processing of personal data. Pseudonymization reduces the regulatory surface area of each API call.
- **Automated decision explainability:** FraudDetectionAgent decisions touching payment + login data are the highest-risk. Field-level scoping limits what the model can base its decision on, making outputs more auditable.
- **Data minimization:** Each agent only receives what it demonstrably needs — principle of least privilege applied to data, not just access control.

---

## Routing rules

### Intent classification (LLM call, structured output)

```
intents: list[Literal["billing", "auth", "product_bug", "outage", "policy"]]
confidence: float  # threshold: 0.7 — below this, route to HumanEscalation
```

### Boundary rules for ambiguous cases

- Account suspended + failed payment → `billing` (auth failure is the symptom, not the cause)
- Issue affects only this user → `product_bug`; widespread impact → `outage`
- "I can't log in" with no account anomaly → `auth`

### Dynamic injection rule

- If `BillingAgent.anomaly_detected == True` → inject `FraudDetectionAgent` after current agent, before resolution
- If `EngineeringAgent.reproduced == False` → replace any remaining agents with `HumanEscalationAgent`
- `outage` intent → skip all other agents, go directly to `HumanEscalationAgent`

### Termination

- Any path that reaches `HumanEscalationAgent` is terminal — no further routing
- Max 5 agent steps per ticket — safety ceiling regardless of routing logic
- `resolved == True` from any agent → ticket exits the chain

---

## Output

Per-ticket resolution object:
```json
{
  "trace_id": "...",
  "intents": ["billing", "technical"],
  "agents_invoked": ["BillingAgent", "FraudDetectionAgent", "EngineeringAgent"],
  "resolution": "escalated | resolved | fraud_flagged",
  "notes": "Accumulated agent notes",
  "steps": 3
}
```

Plus a human-readable execution trace showing the decision path — this is the visual demo artifact.

---

## Storage / infrastructure

**No database.** Everything in-memory for this demo:
- Input: `tickets.json` (flat file, synthetic data)
- Pseudonymization lookup: Python dict, lives for the duration of one run
- Accumulated state per ticket: Python dict, passed forward through agent chain
- Output: `results.json` + printed execution trace

A database would only be warranted if: (a) processing distributed across workers, (b) resuming mid-run across restarts, or (c) querying historical ticket data. None of these apply to a single-run demo.

---

## Success metrics

### Technical correctness
- Classification accuracy: orchestrator assigns correct intent(s) to every synthetic ticket (verifiable by inspection against a labeled expected-output set)
- Routing correctness: each intent maps to the expected agent(s)
- Termination: 100% of tickets reach a terminal state — no ticket loops or hangs
- Chain depth: no ticket exceeds 5-agent ceiling

### Branch coverage (primary demo metric)
Every path below must be exercised by at least one synthetic ticket:

| Path | Ticket that exercises it |
|---|---|
| Single agent, clean resolve | Policy question |
| Multi-agent | `billing` + `auth` together |
| Dynamic injection | Billing anomaly → FraudDetection |
| Loop-back / escalation | Engineering can't reproduce bug |
| Short circuit | Outage → immediate human escalation |
| Low confidence | Ambiguous ticket → human escalation |

### Privacy compliance (verifiable by inspection)
- Zero real identifiers in any agent payload — only `trace_id`
- Zero PII in execution logs
- Each agent receives only its scoped fields per the field-access table

---

## Open decisions

- [ ] Sequential execution for all tickets (simplest — required for fraud injection to work correctly); parallel within a ticket deferred to post-MVP
- [ ] Agents are simulated functions with deterministic logic; one real LLM call in the orchestrator only (classification)
- [ ] Output: CLI execution trace + `results.json`; HTML visualization deferred to post-MVP
