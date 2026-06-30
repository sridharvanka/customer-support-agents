# Technical Blueprint — Customer Support Triage Pipeline

---

## Stack

- **Runtime:** Python 3.11+
- **LLM SDK:** `anthropic` — one API call in the orchestrator (intent classification). All agents are deterministic functions, not LLM calls. A separate `eval.py` adds a second Approach B variant where each agent makes its own LLM call — used only for the eval comparison.
- **No web framework, no database, no task queue.** Single-process, single-run CLI tool.
- **Key dependencies:** `anthropic`, `python-dotenv` (API key from `.env`), `dataclasses` (stdlib), `json` (stdlib)

---

## Resolved open decisions

| Decision | Resolution | Reason |
|---|---|---|
| Parallel vs sequential | Sequential only | Fraud injection requires BillingAgent result before deciding whether to add FraudDetectionAgent — parallelism would break this |
| Agent implementation | Simulated functions (deterministic) | One real LLM call in orchestrator only; keeps demo fast, free, and fully reproducible |
| Output format | CLI execution trace + `results.json` | Sufficient to demonstrate every branch visually; HTML deferred to post-MVP |

---

## File structure

```
customer-support-agents/
│
├── main.py                  # Entry point: load tickets, run pipeline, write output
├── tickets.json             # Synthetic input — 8 tickets, one per branch path
├── results.json             # Generated output (overwritten each run)
├── eval.py                  # Eval runner: compares Approach A vs B on consistency, cost, note quality
├── eval_results.json        # Eval output (overwritten each run)
├── .env                     # ANTHROPIC_API_KEY (gitignored)
├── .env.example             # Template for .env
│
├── models.py                # All dataclasses: RawTicket, ScopedTicket, AgentResult, TicketTrace
│
├── pipeline/
│   ├── __init__.py
│   ├── pseudonymizer.py     # pseudonymize(raw) → (scoped, lookup_table)
│   ├── scope.py             # FIELD_ACCESS dict: maps agent_name → allowed fields
│   ├── orchestrator.py      # classify(ticket) → intents + confidence; build_chain(); maybe_inject()
│   └── agents/
│       ├── __init__.py
│       ├── billing.py       # BillingAgent(scoped, state) → AgentResult
│       ├── auth.py          # AuthAgent(scoped, state) → AgentResult
│       ├── engineering.py   # EngineeringAgent(scoped, state) → AgentResult
│       ├── outage.py        # OutageAgent(scoped, state) → AgentResult (always escalates)
│       ├── policy.py        # PolicyAgent(scoped, state) → AgentResult
│       ├── fraud.py         # FraudDetectionAgent(scoped, state) → AgentResult
│       └── escalation.py    # HumanEscalationAgent(scoped, state) → AgentResult (terminal)
│
└── tracer.py                # print_trace(TicketTrace) + write_results(list[TicketTrace])
```

---

## Data models (`models.py`)

```python
@dataclass
class RawTicket:
    ticket_id: str
    created_at: str
    channel: str            # "email" | "chat" | "web_form"
    subject: str
    body: str
    account: dict           # full account blob including PII

@dataclass
class ScopedTicket:
    trace_id: str           # opaque token — no PII
    subject: str
    body: str
    # account fields present only if allowed by scope.py for this agent:
    subscription_tier: str | None
    account_status: str | None
    recent_payments: list | None
    last_login_at: str | None
    last_login_country: str | None
    # user_id and email are NEVER present here

@dataclass
class AgentResult:
    agent: str              # agent name string
    resolved: bool
    notes: str              # log-safe: decisions only, no PII values
    # optional fields (only populated when relevant):
    anomaly_detected: bool = False
    reproduced: bool | None = None
    risk_level: str | None = None   # "low" | "medium" | "high"
    action_taken: str | None = None
    escalation_id: str | None = None
    policy_cited: str | None = None

@dataclass
class TicketTrace:
    trace_id: str
    ticket_id: str          # re-hydrated at output only
    intents: list[str]
    confidence: float
    agents_invoked: list[str]
    results: list[AgentResult]
    resolution: str         # "resolved" | "escalated" | "fraud_flagged"
    steps: int
```

---

## Module responsibilities

### `pseudonymizer.py`

```python
def pseudonymize(raw: RawTicket) -> tuple[str, dict]:
    """
    Returns (trace_id, identity_map).
    trace_id is a uuid4 string.
    identity_map = { trace_id: { "user_id": ..., "email": ... } }
    The pipeline never touches identity_map — only tracer.py uses it for final output.
    """

def build_scoped_ticket(raw: RawTicket, trace_id: str, agent_name: str) -> ScopedTicket:
    """
    Applies FIELD_ACCESS[agent_name] to strip disallowed fields.
    Returns a ScopedTicket with None for any field the agent isn't allowed to see.
    """
```

### `scope.py`

```python
FIELD_ACCESS: dict[str, set[str]] = {
    "BillingAgent":       {"subject", "body", "subscription_tier", "account_status", "recent_payments"},
    "AuthAgent":          {"subject", "body", "account_status", "last_login_at", "last_login_country"},
    "EngineeringAgent":   {"subject", "body"},
    "OutageAgent":        {"subject", "body"},
    "PolicyAgent":        {"subject", "body", "subscription_tier"},
    "FraudDetectionAgent":{"subject", "body", "subscription_tier", "account_status", "recent_payments", "last_login_at", "last_login_country"},
    "HumanEscalationAgent":{"subject", "body"},
}
```

### `orchestrator.py`

```python
def classify(ticket: RawTicket) -> tuple[list[str], float]:
    """
    Single Anthropic API call. Returns (intents, confidence).
    Uses tool_use / structured output to enforce JSON schema.
    If confidence < 0.7 → returns (["escalate"], 0.0) without routing.
    """

def build_chain(intents: list[str]) -> list[str]:
    """
    Maps intents to ordered agent names.
    "outage" → ["HumanEscalationAgent"] immediately (short-circuit).
    Multiple intents → agents in priority order: billing before auth before product_bug before policy.
    """

def maybe_inject(chain: list[str], last_result: AgentResult) -> list[str]:
    """
    Mutates and returns chain after each agent step.
    Rules:
      - last_result.anomaly_detected == True → insert "FraudDetectionAgent" next
      - last_result.reproduced == False → replace remaining chain with ["HumanEscalationAgent"]
    """
```

### Agents (`pipeline/agents/*.py`)

Each agent is a single function with this signature:

```python
def run(scoped: ScopedTicket, state: dict) -> AgentResult:
```

Agents are deterministic and simulated. They inspect `scoped` fields and `state` (accumulated prior results) to decide their output. No LLM calls inside agents.

**BillingAgent:** Flags `anomaly_detected=True` if `account_status == "suspended"` AND `recent_payments` contains a failed entry.

**AuthAgent:** Flags `resolved=False` if `account_status == "suspended"` (defers to billing to resolve root cause). Otherwise resolves.

**EngineeringAgent:** Flags `reproduced=False` deterministically for the ticket designed to trigger escalation (identified by a marker in the ticket body). Otherwise resolves.

**OutageAgent:** Always returns `resolved=False`, `escalation_id=uuid4()` — immediately terminal.

**PolicyAgent:** Always resolves. Cites a mock policy string.

**FraudDetectionAgent:** Sets `risk_level` based on `last_login_country` mismatch pattern in scoped data. Always terminal.

**HumanEscalationAgent:** Always terminal. Generates `escalation_id`. Summarises prior agent notes into handoff message.

### `tracer.py`

```python
def print_trace(trace: TicketTrace) -> None:
    """Pretty-prints the decision path to stdout. No PII — trace_id only."""

def write_results(traces: list[TicketTrace], path: str = "results.json") -> None:
    """Serialises all TicketTrace objects to results.json."""
```

---

## Pipeline loop (`main.py`)

```python
for raw_ticket in tickets:
    trace_id, identity_map = pseudonymize(raw_ticket)
    intents, confidence = orchestrator.classify(raw_ticket)

    chain = orchestrator.build_chain(intents)
    state = {}
    results = []

    for agent_name in chain[:5]:  # hard ceiling
        scoped = build_scoped_ticket(raw_ticket, trace_id, agent_name)
        result = agents[agent_name].run(scoped, state)
        state[agent_name] = result
        results.append(result)
        chain = orchestrator.maybe_inject(chain, result)
        if is_terminal(agent_name) or result.resolved:
            break

    trace = TicketTrace(...)
    print_trace(trace)
    all_traces.append(trace)

write_results(all_traces)
```

---

## Synthetic ticket design (`tickets.json`)

Eight tickets, one per required branch path:

| # | Intents | Expected path | Tests |
|---|---|---|---|
| 1 | `policy` | PolicyAgent → resolved | Single agent, clean resolve |
| 2 | `billing` (no anomaly) | BillingAgent → resolved | Single agent, billing |
| 3 | `auth` | AuthAgent → resolved | Single agent, auth |
| 4 | `product_bug` (reproducible) | EngineeringAgent → resolved | Single agent, bug |
| 5 | `billing` + `auth` | BillingAgent → AuthAgent → resolved | Multi-agent |
| 6 | `billing` (with anomaly) | BillingAgent → FraudDetectionAgent → terminal | Dynamic injection |
| 7 | `product_bug` (not reproducible) | EngineeringAgent → HumanEscalationAgent | Loop-back / escalation |
| 8 | `outage` | HumanEscalationAgent | Short-circuit |
| 9 | Ambiguous body | confidence < 0.7 → HumanEscalationAgent | Low-confidence fallback |

---

## Build order

Each step is independently runnable/testable before moving to the next.

1. **`models.py`** — define all dataclasses. No logic, just types. Verify with a quick `python models.py`.

2. **`tickets.json`** — write all 9 synthetic tickets by hand. Verify every branch path is covered against the table above before writing any pipeline code.

3. **`pipeline/scope.py`** — the FIELD_ACCESS dict. No imports, pure data. Test by printing `FIELD_ACCESS["BillingAgent"]`.

4. **`pipeline/pseudonymizer.py`** — `pseudonymize()` and `build_scoped_ticket()`. Test: load one raw ticket, pseudonymize it, build a scoped version for BillingAgent, assert `user_id` and `email` are absent.

5. **`pipeline/orchestrator.py` (stub)** — implement `build_chain()` and `maybe_inject()` with a hardcoded mock classify() that returns fixed intents. Test all routing logic without any API calls.

6. **`pipeline/agents/`** — implement all 7 agents. Test each one in isolation with a hand-crafted ScopedTicket. Each should produce the expected AgentResult for its test input.

7. **`pipeline/orchestrator.py` (real classify)** — replace mock classify() with the real Anthropic API call. Test against all 9 tickets. Verify intents match expected.

8. **`tracer.py`** — implement print_trace() and write_results(). Test by running a single ticket end-to-end and reading the output.

9. **`main.py`** — wire everything together. Run all 9 tickets. Verify `results.json` contains 9 traces, each hitting the expected branch.

10. **Branch coverage check** — manually verify every row in the synthetic ticket table produced the expected agent path. This is the success gate.

11. **`eval.py`** — build Approach B agent variants (LLM calls with structured output), wire the eval runner, run 3x per ticket per approach, write `eval_results.json`.

---

## eval.py design

Compares two approaches against all 9 synthetic tickets. Runs each approach 3 times per ticket.

**Approach A:** existing pipeline — one LLM call (classifier), deterministic agents.

**Approach B:** same classifier + same routing, but each agent is replaced by an LLM call with a specialized system prompt. Same output schema (`AgentResult`) so results are directly comparable.

**Metrics collected per ticket per run:**

| Metric | How measured |
|---|---|
| Consistency | Are `resolution` and `agents_invoked` identical across all 3 runs? |
| Token cost | `usage.input_tokens + usage.output_tokens` summed across all LLM calls per ticket |
| Latency | Wall-clock seconds per ticket |
| Note quality | Scored 1–3 by a separate LLM judge call: 1=unhelpful, 2=adequate, 3=clear and actionable |

**What the eval explicitly does NOT claim:**
- Which approach is more "correct" — ground truth doesn't exist for synthetic tickets
- Generalisability to real support tickets

**Output (`eval_results.json`):**
```json
{
  "summary": {
    "approach_a": { "consistency_rate": 1.0, "avg_tokens": 312, "avg_latency_s": 0.8, "avg_note_quality": 1.4 },
    "approach_b": { "consistency_rate": 0.78, "avg_tokens": 2100, "avg_latency_s": 4.2, "avg_note_quality": 2.7 }
  },
  "per_ticket": [ ... ]
}
```

**Narrative framing (for LinkedIn post):**
Approach A wins on cost, latency, and consistency. Approach B wins on note quality. The eval can't tell you which is "right" — and that limitation is itself the insight: synthetic evals only measure the properties of the system, not the quality of its decisions.

---

## What NOT to build in v1

- Parallel agent execution within a ticket
- HTML/visual output
- Web UI or REST API
- Real LLM calls inside agents (classification only)
- Retry logic on Anthropic API failures
- Persistent storage or database
- Multi-ticket concurrency
- Any agent that calls external APIs (billing system, ticketing platform, etc.)
