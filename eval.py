"""
Eval: Approach A (single LLM) vs Approach B (per-agent LLM calls).

Runs all tickets through both approaches 3 times each.
Measures: consistency, token cost, latency, note quality (LLM-judged).

IMPORTANT: This eval cannot measure "correctness" — ground truth doesn't exist
for synthetic tickets. What it measures is the properties of each approach:
  - How stable is the output across runs?
  - How many tokens does each approach consume?
  - How useful are the agent notes?

This limitation is itself the insight: synthetic evals measure system properties,
not decision quality.

Usage:
    python eval.py
"""

import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from dotenv import load_dotenv
import anthropic

from models import RawTicket, ScopedTicket, AgentResult, TicketTrace
from pipeline.pseudonymizer import pseudonymize, build_scoped_ticket
from pipeline.orchestrator import classify, build_chain, maybe_inject, is_terminal
from pipeline.agents import billing, auth, engineering, outage, policy, fraud, escalation
from main import AGENT_REGISTRY, MAX_STEPS, derive_resolution

load_dotenv()

RUNS_PER_TICKET = 3

# ---------------------------------------------------------------------------
# Approach B — LLM-powered agent variants
# ---------------------------------------------------------------------------

_client = None

def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


AGENT_SYSTEM_PROMPTS = {
    "BillingAgent": """You are a billing specialist for a SaaS support team.
Analyse the support ticket and account information provided.
Determine if there is a payment anomaly (suspended account with failed payments = anomaly).
Return JSON only with this schema:
{"resolved": bool, "anomaly_detected": bool, "notes": "one sentence decision summary, no PII"}""",

    "AuthAgent": """You are an authentication specialist for a SaaS support team.
Analyse the support ticket and account status.
If account is suspended, do NOT resolve — billing must fix the root cause first.
Return JSON only:
{"resolved": bool, "notes": "one sentence decision summary, no PII"}""",

    "EngineeringAgent": """You are a technical support engineer for a SaaS product.
Analyse the bug report. If the ticket lacks an error message, steps to reproduce,
or any diagnostic signal, mark reproduced as false.
Return JSON only:
{"resolved": bool, "reproduced": bool, "notes": "one sentence decision summary, no PII"}""",

    "PolicyAgent": """You are a policy specialist for a SaaS support team.
Identify the relevant policy topic and provide a brief mock policy citation.
Return JSON only:
{"resolved": true, "policy_cited": "refund|data|cancellation|general", "notes": "one sentence with policy reference, no PII"}""",

    "FraudDetectionAgent": """You are a fraud analyst for a SaaS support team.
Assess risk based on account suspension, payment failures, and login location.
Return JSON only:
{"risk_level": "low|medium|high", "action_taken": "one sentence action", "notes": "one sentence summary, no PII"}""",

    "HumanEscalationAgent": """You are a support escalation coordinator.
Summarise why this ticket is being escalated and what the human agent needs to know.
Return JSON only:
{"escalation_id": "ESC-DEMO", "notes": "one sentence handoff summary, no PII"}""",
}


def run_llm_agent(agent_name: str, scoped: ScopedTicket, state: dict) -> tuple[AgentResult, int]:
    """
    Approach B: runs an agent as an LLM call.
    Returns (AgentResult, total_tokens_used).
    """
    client = get_client()
    system = AGENT_SYSTEM_PROMPTS.get(agent_name, "You are a support agent. Return JSON.")

    # Build context from scoped ticket (no PII)
    context_parts = [
        f"Subject: {scoped.subject}",
        f"Body: {scoped.body}",
    ]
    if scoped.subscription_tier:
        context_parts.append(f"Subscription tier: {scoped.subscription_tier}")
    if scoped.account_status:
        context_parts.append(f"Account status: {scoped.account_status}")
    if scoped.recent_payments is not None:
        failed = sum(1 for p in scoped.recent_payments if p.get("status") == "failed")
        context_parts.append(f"Failed payments: {failed} of {len(scoped.recent_payments)}")
    if scoped.last_login_country:
        context_parts.append(f"Last login country: {scoped.last_login_country}")
    if state:
        prior = "; ".join(f"{k}: {v.notes[:80]}" for k, v in state.items())
        context_parts.append(f"Prior agent findings: {prior}")

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": "\n".join(context_parts)}],
    )

    tokens = response.usage.input_tokens + response.usage.output_tokens
    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback if LLM output isn't clean JSON
        data = {"resolved": False, "notes": f"Parse error on agent output: {raw_text[:80]}"}

    result = AgentResult(
        agent=agent_name,
        resolved=data.get("resolved", False),
        notes=data.get("notes", ""),
        anomaly_detected=data.get("anomaly_detected", False),
        reproduced=data.get("reproduced"),
        risk_level=data.get("risk_level"),
        action_taken=data.get("action_taken"),
        escalation_id=data.get("escalation_id"),
        policy_cited=data.get("policy_cited"),
    )

    return result, tokens


# ---------------------------------------------------------------------------
# Note quality judge
# ---------------------------------------------------------------------------

def judge_note_quality(agent_name: str, notes: str) -> int:
    """
    Asks a separate LLM to score note quality 1-3.
    1 = unhelpful/vague, 2 = adequate, 3 = clear and actionable.

    This is a meta-eval: we're using an LLM to evaluate LLM output.
    That has its own limitations, but for note quality (not factual correctness)
    it's a reasonable proxy.
    """
    client = get_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        system=(
            "You score support agent notes on a scale of 1-3. "
            "1=unhelpful or vague, 2=adequate but generic, 3=clear and actionable. "
            "Respond with only the number 1, 2, or 3."
        ),
        messages=[{"role": "user", "content": f"Agent: {agent_name}\nNotes: {notes}"}],
    )
    try:
        return int(response.content[0].text.strip()[0])
    except (ValueError, IndexError):
        return 2  # default to adequate if parse fails


# ---------------------------------------------------------------------------
# Run approaches
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    approach: str           # "A" or "B"
    ticket_id: str
    run_number: int
    intents: list[str]
    agents_invoked: list[str]
    resolution: str
    total_tokens: int
    latency_s: float
    note_quality_scores: list[int]  # one score per agent result


def run_approach_a(raw: RawTicket, run_number: int) -> RunResult:
    """Approach A: deterministic agents, one LLM call (classifier)."""
    start = time.time()
    trace_id, _ = pseudonymize(raw)

    # Classify (LLM call — tokens not tracked here for simplicity, same for both)
    intents, confidence = classify(raw)
    chain = build_chain(intents)

    state: dict = {}
    results: list[AgentResult] = []

    for _ in range(MAX_STEPS):
        if not chain:
            break
        agent_name = chain.pop(0)
        run_fn = AGENT_REGISTRY.get(agent_name)
        if not run_fn:
            continue
        scoped = build_scoped_ticket(raw, trace_id, agent_name)
        result = run_fn(scoped, state)
        state[agent_name] = result
        results.append(result)
        chain = maybe_inject(chain, result)
        if is_terminal(agent_name) or result.resolved:
            break

    latency = time.time() - start
    quality_scores = [judge_note_quality(r.agent, r.notes) for r in results]

    return RunResult(
        approach="A",
        ticket_id=raw.ticket_id,
        run_number=run_number,
        intents=intents,
        agents_invoked=[r.agent for r in results],
        resolution=derive_resolution(results),
        total_tokens=0,  # Approach A has zero per-agent tokens
        latency_s=round(latency, 2),
        note_quality_scores=quality_scores,
    )


def run_approach_b(raw: RawTicket, run_number: int) -> RunResult:
    """Approach B: LLM-powered agents, multiple LLM calls."""
    start = time.time()
    trace_id, _ = pseudonymize(raw)
    intents, confidence = classify(raw)
    chain = build_chain(intents)

    state: dict = {}
    results: list[AgentResult] = []
    total_tokens = 0

    for _ in range(MAX_STEPS):
        if not chain:
            break
        agent_name = chain.pop(0)
        scoped = build_scoped_ticket(raw, trace_id, agent_name)
        result, tokens = run_llm_agent(agent_name, scoped, state)
        total_tokens += tokens
        state[agent_name] = result
        results.append(result)
        chain = maybe_inject(chain, result)
        if is_terminal(agent_name) or result.resolved:
            break

    latency = time.time() - start
    quality_scores = [judge_note_quality(r.agent, r.notes) for r in results]

    return RunResult(
        approach="B",
        ticket_id=raw.ticket_id,
        run_number=run_number,
        intents=intents,
        agents_invoked=[r.agent for r in results],
        resolution=derive_resolution(results),
        total_tokens=total_tokens,
        latency_s=round(latency, 2),
        note_quality_scores=quality_scores,
    )


# ---------------------------------------------------------------------------
# Consistency check
# ---------------------------------------------------------------------------

def check_consistency(runs: list[RunResult]) -> bool:
    """Returns True if all runs produced identical agents_invoked and resolution."""
    if not runs:
        return True
    first = runs[0]
    return all(
        r.agents_invoked == first.agents_invoked and r.resolution == first.resolution
        for r in runs[1:]
    )


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def main():
    print("Eval: Approach A vs Approach B")
    print("=" * 60)
    print(f"Runs per ticket per approach: {RUNS_PER_TICKET}")
    print()
    print("NOTE: This eval measures consistency, cost, and note quality.")
    print("It does NOT measure correctness — ground truth doesn't exist for synthetic tickets.")
    print("=" * 60)

    with open("tickets.json") as f:
        raw_data = json.load(f)
    tickets = [RawTicket(**t) for t in raw_data]

    all_results_a: list[RunResult] = []
    all_results_b: list[RunResult] = []

    for raw in tickets:
        print(f"\nTicket {raw.ticket_id}: {raw.subject[:50]}")

        # Run Approach A
        runs_a = []
        for i in range(RUNS_PER_TICKET):
            print(f"  A run {i+1}…", end=" ", flush=True)
            result = run_approach_a(raw, i + 1)
            runs_a.append(result)
            print(f"{result.resolution} {result.agents_invoked}")
        all_results_a.extend(runs_a)

        # Run Approach B
        runs_b = []
        for i in range(RUNS_PER_TICKET):
            print(f"  B run {i+1}…", end=" ", flush=True)
            result = run_approach_b(raw, i + 1)
            runs_b.append(result)
            print(f"{result.resolution} {result.agents_invoked}")

        all_results_b.extend(runs_b)

        # Consistency per ticket
        consistent_a = check_consistency(runs_a)
        consistent_b = check_consistency(runs_b)
        print(f"  Consistency — A: {'✓' if consistent_a else '✗'}  B: {'✓' if consistent_b else '✗'}")

    # Aggregate summary
    def summarise(runs: list[RunResult]) -> dict:
        per_ticket: dict[str, list[RunResult]] = {}
        for r in runs:
            per_ticket.setdefault(r.ticket_id, []).append(r)

        consistency_rate = sum(
            1 for ticket_runs in per_ticket.values() if check_consistency(ticket_runs)
        ) / len(per_ticket)

        all_quality = [s for r in runs for s in r.note_quality_scores]
        avg_quality = round(sum(all_quality) / len(all_quality), 2) if all_quality else 0

        token_runs = [r for r in runs if r.total_tokens > 0]
        avg_tokens = round(sum(r.total_tokens for r in token_runs) / len(token_runs), 0) if token_runs else 0

        avg_latency = round(sum(r.latency_s for r in runs) / len(runs), 2)

        return {
            "consistency_rate": round(consistency_rate, 2),
            "avg_tokens_per_ticket": avg_tokens,
            "avg_latency_s": avg_latency,
            "avg_note_quality": avg_quality,
        }

    summary_a = summarise(all_results_a)
    summary_b = summarise(all_results_b)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Metric':<30} {'Approach A':>12} {'Approach B':>12}")
    print("-" * 56)
    print(f"{'Consistency rate':<30} {summary_a['consistency_rate']:>12.0%} {summary_b['consistency_rate']:>12.0%}")
    print(f"{'Avg tokens/ticket':<30} {summary_a['avg_tokens_per_ticket']:>12.0f} {summary_b['avg_tokens_per_ticket']:>12.0f}")
    print(f"{'Avg latency (s)':<30} {summary_a['avg_latency_s']:>12.2f} {summary_b['avg_latency_s']:>12.2f}")
    print(f"{'Avg note quality (1-3)':<30} {summary_a['avg_note_quality']:>12.2f} {summary_b['avg_note_quality']:>12.2f}")

    print("\nCAVEAT: Note quality is scored by an LLM judge — itself a limitation.")
    print("Consistency and cost are objective. Note quality is indicative only.")

    output = {
        "eval_metadata": {
            "runs_per_ticket": RUNS_PER_TICKET,
            "total_tickets": len(tickets),
            "caveat": (
                "This eval measures consistency, cost, and note quality only. "
                "It cannot measure correctness — no ground truth exists for synthetic tickets."
            ),
        },
        "summary": {
            "approach_a": summary_a,
            "approach_b": summary_b,
        },
        "per_run": {
            "approach_a": [asdict(r) for r in all_results_a],
            "approach_b": [asdict(r) for r in all_results_b],
        },
    }

    with open("eval_results.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print("\nFull results written to eval_results.json")


if __name__ == "__main__":
    main()
