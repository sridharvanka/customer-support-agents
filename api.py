"""
Customer Support Triage Pipeline — FastAPI wrapper for Railway deployment.

Endpoints:
  GET  /          HTML dashboard (visual demo)
  GET  /health    Railway health check
  GET  /run       Process all tickets from tickets.json → JSON
  POST /triage    Process a single ticket body → JSON
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from models import RawTicket, AgentResult, TicketTrace
from pipeline.pseudonymizer import pseudonymize, build_scoped_ticket
from pipeline.orchestrator import classify, build_chain, maybe_inject, is_terminal
from pipeline.agents import billing, auth, engineering, outage, policy, fraud, escalation
from main import AGENT_REGISTRY, MAX_STEPS, derive_resolution


# ---------------------------------------------------------------------------
# Pipeline runner (sync — runs in threadpool via FastAPI)
# ---------------------------------------------------------------------------

def run_pipeline(raw: RawTicket) -> dict:
    trace_id, identity_map = pseudonymize(raw)
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

    resolution = derive_resolution(results)
    all_notes = " | ".join(r.notes for r in results if r.notes)

    trace = TicketTrace(
        trace_id=trace_id,
        ticket_id=raw.ticket_id,
        intents=intents,
        confidence=confidence,
        agents_invoked=[r.agent for r in results],
        results=results,
        resolution=resolution,
        steps=len(results),
        notes=all_notes,
    )

    d = asdict(trace)
    d["ticket_id"] = identity_map[trace_id]["ticket_id"]
    return d


def load_tickets() -> list[RawTicket]:
    tickets_path = os.path.join(os.path.dirname(__file__), "tickets.json")
    with open(tickets_path) as f:
        return [RawTicket(**t) for t in json.load(f)]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Support Triage Pipeline", docs_url="/docs")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/run")
def run_all():
    """Process all tickets from tickets.json and return traces."""
    tickets = load_tickets()
    results = []
    for raw in tickets:
        try:
            results.append(run_pipeline(raw))
        except Exception as e:
            results.append({"ticket_id": raw.ticket_id, "error": str(e)})
    return JSONResponse(content={"total": len(results), "tickets": results})


@app.post("/triage")
async def triage_one(request: Request):
    """Process a single ticket. Accepts raw ticket JSON body."""
    try:
        body = await request.json()
        raw = RawTicket(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid ticket format: {e}")
    try:
        result = run_pipeline(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# HTML Dashboard — GET /
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Support Triage Pipeline — Sridhar Vanka</title>
<style>
  :root {
    --bg: #0c0c0c;
    --bg2: #141414;
    --bg3: #1c1c1c;
    --border: #282828;
    --text: #e0e0e0;
    --text-dim: #666;
    --text-mid: #999;
    --green: #4ade80;
    --amber: #f59e0b;
    --red: #ef4444;
    --blue: #60a5fa;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --font-mono: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-sans); min-height: 100vh; }

  nav {
    display: flex; align-items: center; justify-content: space-between;
    padding: 1.25rem 2rem; border-bottom: 1px solid var(--border);
    position: sticky; top: 0; background: var(--bg); z-index: 10;
  }
  .nav-logo { font-size: 0.75rem; font-weight: 600; letter-spacing: 0.15em; text-transform: uppercase; color: var(--text); text-decoration: none; }
  .nav-links { display: flex; gap: 1.5rem; }
  .nav-links a { font-size: 0.8rem; color: var(--text-mid); text-decoration: none; transition: color .15s; }
  .nav-links a:hover { color: var(--text); }

  .hero { padding: 4rem 2rem 3rem; max-width: 860px; margin: 0 auto; }
  .hero-label { font-size: 0.7rem; letter-spacing: 0.15em; text-transform: uppercase; color: var(--text-dim); margin-bottom: 1rem; }
  .hero h1 { font-size: clamp(1.8rem, 4vw, 2.6rem); font-weight: 600; line-height: 1.2; margin-bottom: 1rem; }
  .hero h1 em { font-style: italic; color: var(--text-mid); }
  .hero p { font-size: 0.95rem; color: var(--text-mid); line-height: 1.7; max-width: 540px; margin-bottom: 2rem; }

  .stats { display: flex; gap: 2rem; margin-bottom: 2.5rem; flex-wrap: wrap; }
  .stat { }
  .stat-value { font-size: 1.6rem; font-weight: 700; font-family: var(--font-mono); }
  .stat-label { font-size: 0.65rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-dim); margin-top: 0.2rem; }

  .run-btn {
    display: inline-flex; align-items: center; gap: 0.5rem;
    background: var(--text); color: var(--bg); font-family: var(--font-sans);
    font-size: 0.8rem; font-weight: 600; letter-spacing: 0.05em;
    padding: 0.65rem 1.4rem; border: none; cursor: pointer;
    transition: opacity .15s; text-transform: uppercase;
  }
  .run-btn:hover { opacity: 0.85; }
  .run-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .pipeline-steps {
    display: flex; align-items: center; gap: 0; flex-wrap: wrap;
    margin: 3rem auto 0; max-width: 860px; padding: 0 2rem;
    border-top: 1px solid var(--border); padding-top: 2rem;
  }
  .step { display: flex; align-items: center; gap: 0.4rem; }
  .step-num { font-size: 0.65rem; font-family: var(--font-mono); color: var(--text-dim); }
  .step-name { font-size: 0.75rem; color: var(--text-mid); }
  .step-arrow { color: var(--text-dim); margin: 0 0.3rem; font-size: 0.75rem; }

  .results { max-width: 860px; margin: 0 auto; padding: 2rem; }
  .results-header { font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-dim); margin-bottom: 1.25rem; }

  .ticket-card {
    background: var(--bg2); border: 1px solid var(--border);
    margin-bottom: 1rem; padding: 1.25rem 1.5rem;
    transition: border-color .15s;
  }
  .ticket-card:hover { border-color: #3a3a3a; }

  .ticket-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: 0.75rem; }
  .ticket-id { font-size: 0.65rem; font-family: var(--font-mono); color: var(--text-dim); }
  .ticket-subject { font-size: 0.9rem; font-weight: 500; margin-top: 0.2rem; }
  .resolution-badge {
    font-size: 0.65rem; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; padding: 0.25rem 0.6rem;
    flex-shrink: 0;
  }
  .badge-resolved { background: rgba(74,222,128,0.12); color: var(--green); border: 1px solid rgba(74,222,128,0.25); }
  .badge-escalated { background: rgba(245,158,11,0.12); color: var(--amber); border: 1px solid rgba(245,158,11,0.25); }
  .badge-fraud_flagged { background: rgba(239,68,68,0.12); color: var(--red); border: 1px solid rgba(239,68,68,0.25); }

  .ticket-meta { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .intent-tag {
    font-size: 0.65rem; font-family: var(--font-mono); color: var(--blue);
    background: rgba(96,165,250,0.08); border: 1px solid rgba(96,165,250,0.2);
    padding: 0.15rem 0.45rem;
  }
  .confidence { font-size: 0.7rem; color: var(--text-dim); font-family: var(--font-mono); }

  .agent-chain { display: flex; align-items: center; gap: 0; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .agent-chip {
    font-size: 0.7rem; font-family: var(--font-mono);
    padding: 0.2rem 0.5rem; background: var(--bg3);
    border: 1px solid var(--border); color: var(--text-mid);
  }
  .agent-chip.terminal { color: var(--amber); border-color: rgba(245,158,11,0.3); }
  .agent-chip.fraud { color: var(--red); border-color: rgba(239,68,68,0.3); }
  .chain-arrow { color: var(--text-dim); font-size: 0.7rem; margin: 0 0.2rem; }

  .ticket-notes {
    font-size: 0.75rem; color: var(--text-dim); font-family: var(--font-mono);
    line-height: 1.6; background: var(--bg); padding: 0.75rem; border-left: 2px solid var(--border);
    white-space: pre-wrap; word-break: break-word;
  }

  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--bg); border-top-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-msg { color: var(--red); font-size: 0.8rem; font-family: var(--font-mono); padding: 1rem; background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.2); }

  .triage-section { max-width: 860px; margin: 0 auto; padding: 0 2rem 2rem; }
  .triage-label { font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-dim); margin-bottom: 1rem; }
  .triage-form { display: flex; flex-direction: column; gap: 0.75rem; }
  .triage-form textarea {
    background: var(--bg2); border: 1px solid var(--border); color: var(--text);
    font-family: var(--font-mono); font-size: 0.75rem; padding: 0.75rem;
    resize: vertical; min-height: 140px; outline: none;
  }
  .triage-form textarea:focus { border-color: #444; }
  .form-row { display: flex; gap: 0.75rem; align-items: center; }
  .triage-btn {
    background: transparent; border: 1px solid var(--border); color: var(--text-mid);
    font-family: var(--font-sans); font-size: 0.75rem; letter-spacing: 0.08em;
    text-transform: uppercase; padding: 0.55rem 1.2rem; cursor: pointer; transition: all .15s;
  }
  .triage-btn:hover { border-color: #555; color: var(--text); }
  .triage-result { margin-top: 0.75rem; }

  footer {
    border-top: 1px solid var(--border); padding: 1.5rem 2rem;
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;
    margin-top: 3rem;
  }
  .footer-logo { font-size: 0.7rem; letter-spacing: 0.15em; text-transform: uppercase; color: var(--text-dim); }
  .footer-links { display: flex; gap: 1rem; }
  .footer-links a { font-size: 0.75rem; color: var(--text-dim); text-decoration: none; }
  .footer-links a:hover { color: var(--text); }
</style>
</head>
<body>

<nav>
  <a class="nav-logo" href="https://sridharvanka.me">SRIDHAR VANKA</a>
  <div class="nav-links">
    <a href="https://sridharvanka.me/#writing">Writing</a>
    <a href="https://sridharvanka.me/build.html">Building</a>
    <a href="https://sridharvanka.me/#work">Projects</a>
    <a href="https://sridharvanka.me/aboutme.html">About</a>
    <a href="mailto:sridhar.vanka@gmail.com">Get in touch</a>
  </div>
</nav>

<div class="hero">
  <div class="hero-label">Demo project</div>
  <h1>Customer support triage,<br><em>routed by AI.</em></h1>
  <p>An orchestrator-workers pipeline that classifies support tickets and routes them through specialist agents dynamically — with privacy-preserving pseudonymization built in from the start.</p>

  <div class="stats">
    <div class="stat"><div class="stat-value">7</div><div class="stat-label">Agents</div></div>
    <div class="stat"><div class="stat-value">1</div><div class="stat-label">LLM call</div></div>
    <div class="stat"><div class="stat-value">5</div><div class="stat-label">Intent types</div></div>
    <div class="stat"><div class="stat-value">0</div><div class="stat-label">PII in agent payloads</div></div>
  </div>

  <button class="run-btn" id="runBtn" onclick="runAll()">
    <span id="runBtnText">Run pipeline →</span>
  </button>
</div>

<div class="pipeline-steps">
  <div class="step"><span class="step-num">01</span><span class="step-name">Classify</span></div>
  <span class="step-arrow">→</span>
  <div class="step"><span class="step-num">02</span><span class="step-name">Route</span></div>
  <span class="step-arrow">→</span>
  <div class="step"><span class="step-num">03</span><span class="step-name">Agent(s)</span></div>
  <span class="step-arrow">→</span>
  <div class="step"><span class="step-num">04</span><span class="step-name">Inject?</span></div>
  <span class="step-arrow">→</span>
  <div class="step"><span class="step-num">05</span><span class="step-name">Resolve or escalate</span></div>
</div>

<div class="results" id="results" style="display:none">
  <div class="results-header" id="resultsHeader"></div>
  <div id="ticketCards"></div>
</div>

<div class="triage-section" style="margin-top:2rem">
  <div class="triage-label">Try a single ticket → POST /triage</div>
  <div class="triage-form">
    <textarea id="triageInput" placeholder='{"ticket_id":"TKT-X","created_at":"2025-06-30T10:00:00Z","channel":"chat","subject":"I was charged twice","body":"I see two $29.99 charges this month.","account":{"user_id":"USR-X","email":"test@example.com","subscription_tier":"pro","account_status":"active","recent_payments":[{"date":"2025-06-01","amount":29.99,"status":"success"},{"date":"2025-06-03","amount":29.99,"status":"success"}],"last_login_at":"2025-06-30T09:00:00Z","last_login_country":"US"}}'></textarea>
    <div class="form-row">
      <button class="triage-btn" id="triageBtn" onclick="triageOne()">Triage this ticket →</button>
      <span id="triageStatus" style="font-size:0.75rem;color:var(--text-dim)"></span>
    </div>
    <div id="triageResult" class="triage-result"></div>
  </div>
</div>

<footer>
  <span class="footer-logo">Sridhar Vanka</span>
  <div class="footer-links">
    <a href="/docs">API docs →</a>
    <a href="https://github.com/sridharvanka">GitHub ↗</a>
    <a href="https://sridharvanka.me">Portfolio ↗</a>
  </div>
</footer>

<script>
const TERMINAL_AGENTS = new Set(["HumanEscalationAgent", "OutageAgent", "FraudDetectionAgent"]);

function agentClass(name) {
  if (name === "FraudDetectionAgent") return "fraud";
  if (TERMINAL_AGENTS.has(name)) return "terminal";
  return "";
}

function renderCard(t) {
  const intents = (t.intents || []).map(i => `<span class="intent-tag">${i}</span>`).join("");
  const chain = (t.agents_invoked || []).map((a, i) => {
    const cls = agentClass(a);
    const arrow = i < t.agents_invoked.length - 1 ? '<span class="chain-arrow">→</span>' : "";
    return `<span class="agent-chip ${cls}">${a.replace("Agent","")}</span>${arrow}`;
  }).join("");
  const badgeCls = `badge-${t.resolution}`;
  const notes = (t.notes || "").split(" | ").map(n => `  ${n}`).join("\\n");
  return `
    <div class="ticket-card">
      <div class="ticket-top">
        <div>
          <div class="ticket-id">${t.ticket_id} &nbsp;·&nbsp; ${t.steps} step${t.steps !== 1 ? "s" : ""}</div>
          <div class="ticket-subject">${escHtml(t.subject || "")}</div>
        </div>
        <span class="resolution-badge ${badgeCls}">${t.resolution.replace("_"," ")}</span>
      </div>
      <div class="ticket-meta">${intents}<span class="confidence">confidence ${(t.confidence * 100).toFixed(0)}%</span></div>
      <div class="agent-chain">${chain}</div>
      <div class="ticket-notes">${escHtml(notes)}</div>
    </div>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

async function runAll() {
  const btn = document.getElementById("runBtn");
  const txt = document.getElementById("runBtnText");
  btn.disabled = true;
  txt.innerHTML = '<span class="spinner"></span>&nbsp; Running…';
  document.getElementById("results").style.display = "none";

  try {
    const res = await fetch("/run");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Pipeline error");
    const tickets = data.tickets || [];
    const resolved = tickets.filter(t => t.resolution === "resolved").length;
    const escalated = tickets.filter(t => t.resolution === "escalated").length;
    const fraud = tickets.filter(t => t.resolution === "fraud_flagged").length;
    document.getElementById("resultsHeader").textContent =
      `${tickets.length} tickets processed  ·  ${resolved} resolved  ·  ${escalated} escalated  ·  ${fraud} fraud flagged`;
    document.getElementById("ticketCards").innerHTML = tickets.map(renderCard).join("");
    document.getElementById("results").style.display = "block";
  } catch(e) {
    document.getElementById("ticketCards").innerHTML = `<div class="error-msg">Error: ${escHtml(e.message)}</div>`;
    document.getElementById("results").style.display = "block";
  } finally {
    btn.disabled = false;
    txt.textContent = "Run again →";
  }
}

async function triageOne() {
  const btn = document.getElementById("triageBtn");
  const status = document.getElementById("triageStatus");
  const input = document.getElementById("triageInput").value.trim();
  if (!input) return;
  btn.disabled = true;
  status.textContent = "Processing…";

  try {
    const res = await fetch("/triage", { method: "POST", headers: {"Content-Type":"application/json"}, body: input });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Error");
    document.getElementById("triageResult").innerHTML = renderCard(data);
    status.textContent = "";
  } catch(e) {
    document.getElementById("triageResult").innerHTML = `<div class="error-msg">Error: ${escHtml(e.message)}</div>`;
    status.textContent = "";
  } finally {
    btn.disabled = false;
  }
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)
