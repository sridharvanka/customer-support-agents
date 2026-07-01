"""
Customer Support Triage Pipeline — FastAPI wrapper for Railway deployment.

Endpoints:
  GET  /          HTML dashboard (visual demo)
  GET  /health    Railway health check
  GET  /tickets   Ticket metadata (no PII)
  GET  /run       Process all tickets from tickets.json -> JSON
  POST /triage    Process a single ticket body -> JSON
"""

import json
import os
import sys
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
    d["subject"] = raw.subject
    d["channel"] = raw.channel
    return d


def load_tickets() -> list[RawTicket]:
    tickets_path = os.path.join(os.path.dirname(__file__), "tickets.json")
    with open(tickets_path) as f:
        return [RawTicket(**t) for t in json.load(f)]


app = FastAPI(title="Support Triage Pipeline", docs_url="/docs")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tickets")
def list_tickets():
    tickets = load_tickets()
    return JSONResponse(content=[
        {"ticket_id": t.ticket_id, "subject": t.subject, "channel": t.channel}
        for t in tickets
    ])


@app.get("/run")
def run_all():
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


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Support Triage Pipeline &mdash; Sridhar Vanka</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --paper:       #F4F1E8;
    --surface:     #FFFFFF;
    --inset:       #FAF8F2;
    --accent:      #0E8A6E;
    --accent-ink:  #0A5C49;
    --ink:         #14130F;
    --ink-muted:   #57544B;
    --ink-faint:   #8C887C;
    --line:        rgba(20,19,15,0.11);
    --line-soft:   rgba(20,19,15,0.07);
    --chip-bg:     #F1EEE4;
    --radius:      14px;
    --font-sans:   'Archivo', 'Helvetica Neue', Arial, sans-serif;
    --font-mono:   'JetBrains Mono', monospace;
  }

  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--paper);
    color: var(--ink);
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
    overflow-x: hidden;
  }
  a { text-decoration: none; color: inherit; }
  ::selection { background: var(--accent); color: var(--paper); }

  /* Grid background */
  .grid-bg {
    position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background-image:
      linear-gradient(rgba(20,19,15,0.045) 1px, transparent 1px),
      linear-gradient(90deg, rgba(20,19,15,0.045) 1px, transparent 1px);
    background-size: 32px 32px;
    mask-image: radial-gradient(120% 120% at 50% 0%, #000 60%, transparent 100%);
    -webkit-mask-image: radial-gradient(120% 120% at 50% 0%, #000 60%, transparent 100%);
  }
  .site-wrap { position: relative; z-index: 1; }

  /* Nav */
  .nav-header {
    position: sticky; top: 0; z-index: 20;
    backdrop-filter: saturate(140%) blur(8px);
    -webkit-backdrop-filter: saturate(140%) blur(8px);
    background: rgba(244,241,232,0.78);
    border-bottom: 1px solid var(--line-soft);
  }
  .nav-inner {
    max-width: 1180px; margin: 0 auto; padding: 16px 32px;
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
  }
  .nav-wordmark { font-weight: 800; font-size: 15px; letter-spacing: 0.08em; }
  .nav-links { display: flex; align-items: center; gap: 28px; }
  .nav-link { font-size: 14.5px; font-weight: 500; color: var(--ink-muted); transition: color 0.15s; }
  .nav-link:hover { color: var(--ink); }
  .nav-cta {
    font-size: 14px; font-weight: 600; color: var(--paper);
    background: var(--ink); padding: 9px 16px; border-radius: 999px; transition: opacity 0.15s;
  }
  .nav-cta:hover { opacity: 0.85; }

  /* Main layout */
  .main { max-width: 860px; margin: 0 auto; padding: 0 32px; }

  /* Hero */
  .hero { padding: 64px 0 48px; }
  .eyebrow {
    font-family: var(--font-mono); font-size: 12.5px; font-weight: 600;
    letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent-ink);
  }
  .hero-h1 {
    margin: 16px 0 0;
    font-size: clamp(2rem, 5vw, 3.2rem);
    font-weight: 800; line-height: 1.05; letter-spacing: -0.03em;
  }
  .hero-h1 em { font-style: italic; color: var(--ink-muted); }
  .hero-lead {
    margin: 16px 0 0; max-width: 540px; font-size: 17px;
    line-height: 1.6; color: var(--ink-muted); font-weight: 450;
  }

  /* Stats */
  .stats { display: flex; gap: 2.5rem; margin: 2rem 0; flex-wrap: wrap; }
  .stat-value { font-size: 2rem; font-weight: 800; font-family: var(--font-sans); letter-spacing: -0.03em; }
  .stat-label { font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-faint); margin-top: 2px; }

  /* Run button */
  .run-btn {
    display: inline-flex; align-items: center; gap: 0.5rem;
    background: var(--ink); color: var(--paper);
    font-family: var(--font-sans); font-size: 15px; font-weight: 700;
    padding: 11px 22px; border: none; border-radius: 999px;
    cursor: pointer; transition: opacity .15s; letter-spacing: 0.01em;
  }
  .run-btn:hover { opacity: 0.85; }
  .run-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Pipeline steps */
  .pipeline-steps {
    display: flex; align-items: center; flex-wrap: wrap; gap: 0;
    margin: 2.5rem 0 0; padding: 1.5rem 0 0; border-top: 1px solid var(--line-soft);
  }
  .step { display: flex; align-items: center; gap: 6px; }
  .step-num { font-family: var(--font-mono); font-size: 11px; color: var(--accent-ink); font-weight: 600; }
  .step-name { font-size: 13px; font-weight: 600; color: var(--ink-muted); }
  .step-arrow { color: var(--ink-faint); margin: 0 6px; font-size: 13px; }

  /* Queue */
  .queue-section { margin: 2rem 0 0; }
  .section-label {
    font-family: var(--font-mono); font-size: 12px; font-weight: 600;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-faint);
    margin-bottom: 12px;
  }
  .queue-list { display: flex; flex-direction: column; gap: 6px; }
  .queue-row {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 14px;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px;
    transition: box-shadow 0.15s;
  }
  .queue-row:hover { box-shadow: 0 2px 8px rgba(20,19,15,0.06); }
  .queue-id { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-faint); min-width: 62px; }
  .queue-channel {
    font-family: var(--font-mono); font-size: 11px; font-weight: 600;
    color: var(--accent-ink); background: #E7F0EC;
    border: 1px solid rgba(14,138,110,0.25);
    padding: 2px 8px; border-radius: 6px; flex-shrink: 0;
  }
  .queue-subject { font-size: 14px; font-weight: 500; color: var(--ink-muted); flex: 1; }

  /* Results */
  .results { margin: 2rem 0 0; }
  .results-header {
    font-family: var(--font-mono); font-size: 12px; font-weight: 600;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--ink-faint); margin-bottom: 16px;
  }

  /* Ticket card */
  .ticket-card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: 0 1px 2px rgba(20,19,15,0.04), 0 8px 24px -12px rgba(20,19,15,0.14);
    margin-bottom: 14px; padding: 20px 22px;
    transition: box-shadow 0.2s, transform 0.2s;
  }
  .ticket-card:hover {
    box-shadow: 0 1px 2px rgba(20,19,15,0.06), 0 14px 32px -10px rgba(20,19,15,0.20);
    transform: translateY(-1px);
  }

  .ticket-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
  .ticket-id { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-faint); }
  .ticket-subject { font-size: 16px; font-weight: 700; margin-top: 3px; letter-spacing: -0.01em; }

  .resolution-badge { font-family: var(--font-mono); font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; padding: 4px 10px; border-radius: 8px; flex-shrink: 0; }
  .badge-resolved { background: #E7F0EC; color: var(--accent-ink); border: 1px solid rgba(14,138,110,0.25); }
  .badge-escalated { background: #FEF3C7; color: #92400E; border: 1px solid rgba(217,119,6,0.25); }
  .badge-fraud_flagged { background: #FEE2E2; color: #991B1B; border: 1px solid rgba(220,38,38,0.25); }

  .ticket-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
  .intent-tag {
    font-family: var(--font-mono); font-size: 11px; font-weight: 600;
    color: var(--accent-ink); background: #E7F0EC;
    border: 1px solid rgba(14,138,110,0.25); padding: 3px 8px; border-radius: 6px;
  }
  .confidence { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-faint); }

  .agent-chain { display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .agent-chip {
    font-family: var(--font-mono); font-size: 11.5px; font-weight: 500;
    padding: 3px 10px; background: var(--chip-bg);
    border: 1px solid var(--line); border-radius: 6px; color: var(--ink-muted);
  }
  .agent-chip.terminal { color: #92400E; background: #FEF3C7; border-color: rgba(217,119,6,0.25); }
  .agent-chip.fraud { color: #991B1B; background: #FEE2E2; border-color: rgba(220,38,38,0.25); }
  .chain-arrow { color: var(--ink-faint); font-size: 12px; margin: 0 4px; }

  .ticket-notes {
    font-family: var(--font-mono); font-size: 12.5px; color: var(--ink-muted);
    line-height: 1.7; background: var(--inset); padding: 12px 14px;
    border-radius: 10px; border: 1px solid var(--line-soft);
    white-space: pre-wrap; word-break: break-word;
  }

  /* Spinner */
  .spinner { display: inline-block; width: 13px; height: 13px; border: 2px solid var(--paper); border-top-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-msg {
    font-family: var(--font-mono); font-size: 13px; color: #991B1B;
    padding: 14px 16px; background: #FEE2E2; border: 1px solid rgba(220,38,38,0.25);
    border-radius: 10px;
  }

  /* Single triage */
  .triage-section { margin: 2.5rem 0 0; padding-top: 2rem; border-top: 1px solid var(--line-soft); }
  .triage-form { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
  .triage-form textarea {
    background: var(--surface); border: 1px solid var(--line); color: var(--ink);
    font-family: var(--font-mono); font-size: 12.5px; padding: 14px 16px;
    border-radius: 10px; resize: vertical; min-height: 130px; outline: none;
    transition: border-color 0.15s;
  }
  .triage-form textarea:focus { border-color: var(--accent); }
  .form-row { display: flex; gap: 10px; align-items: center; }
  .triage-btn {
    background: transparent; border: 1px solid var(--line); color: var(--ink-muted);
    font-family: var(--font-sans); font-size: 14px; font-weight: 600;
    padding: 9px 18px; border-radius: 999px; cursor: pointer; transition: all .15s;
  }
  .triage-btn:hover { border-color: var(--ink-muted); color: var(--ink); }
  .triage-result { margin-top: 10px; }

  /* Footer */
  footer {
    max-width: 860px; margin: 4rem auto 0; padding: 2rem 32px 3.5rem;
    border-top: 1px solid var(--line);
    display: flex; align-items: flex-end; justify-content: space-between;
    gap: 24px; flex-wrap: wrap;
  }
  .footer-wordmark { font-weight: 800; font-size: 15px; letter-spacing: 0.08em; }
  .footer-copy { margin-top: 4px; font-size: 13px; color: var(--ink-faint); }
  .footer-links { display: flex; gap: 16px; }
  .footer-links a {
    font-family: var(--font-mono); font-size: 12.5px; color: var(--accent-ink);
    border-bottom: 2px solid var(--accent); padding-bottom: 2px;
    transition: opacity 0.15s;
  }
  .footer-links a:hover { opacity: 0.7; }

  @media (max-width: 640px) {
    .nav-inner { padding: 14px 20px; }
    .main { padding: 0 20px; }
    footer { padding: 2rem 20px 3rem; }
    .nav-links { gap: 16px; }
  }
</style>
</head>
<body>

<div class="grid-bg"></div>
<div class="site-wrap">

<header class="nav-header">
  <div class="nav-inner">
    <a class="nav-wordmark" href="https://sridharvanka.me">SRIDHAR VANKA</a>
    <nav class="nav-links">
      <a class="nav-link" href="https://sridharvanka.me/#writing">Writing</a>
      <a class="nav-link" href="https://sridharvanka.me/build.html">Building</a>
      <a class="nav-link" href="https://sridharvanka.me/#work">Projects</a>
      <a class="nav-link" href="https://sridharvanka.me/aboutme.html">About</a>
      <a class="nav-cta" href="mailto:sridhar.vanka@gmail.com">Get in touch</a>
    </nav>
  </div>
</header>

<main class="main">

  <div class="hero">
    <div class="eyebrow">Demo project</div>
    <h1 class="hero-h1">Customer support triage,<br><em>routed by AI.</em></h1>
    <p class="hero-lead">An orchestrator-workers pipeline that classifies support tickets and routes them through specialist agents dynamically &mdash; with privacy-preserving pseudonymization built in from the start.</p>

    <div class="stats">
      <div class="stat"><div class="stat-value">7</div><div class="stat-label">Agents</div></div>
      <div class="stat"><div class="stat-value">1</div><div class="stat-label">LLM call</div></div>
      <div class="stat"><div class="stat-value">5</div><div class="stat-label">Intent types</div></div>
      <div class="stat"><div class="stat-value">0</div><div class="stat-label">PII in agent payloads</div></div>
    </div>

    <button class="run-btn" id="runBtn" onclick="runAll()">
      <span id="runBtnText">Run pipeline &#8594;</span>
    </button>

    <div class="pipeline-steps">
      <div class="step"><span class="step-num">01</span><span class="step-name">Classify</span></div>
      <span class="step-arrow">&#8594;</span>
      <div class="step"><span class="step-num">02</span><span class="step-name">Route</span></div>
      <span class="step-arrow">&#8594;</span>
      <div class="step"><span class="step-num">03</span><span class="step-name">Agent(s)</span></div>
      <span class="step-arrow">&#8594;</span>
      <div class="step"><span class="step-num">04</span><span class="step-name">Inject?</span></div>
      <span class="step-arrow">&#8594;</span>
      <div class="step"><span class="step-num">05</span><span class="step-name">Resolve or escalate</span></div>
    </div>
  </div>

  <div class="queue-section" id="queueSection">
    <div class="section-label" id="queueHeader">Loading tickets&hellip;</div>
    <div class="queue-list" id="queueList"></div>
  </div>

  <div class="results" id="results" style="display:none">
    <div class="results-header" id="resultsHeader"></div>
    <div id="ticketCards"></div>
  </div>

  <div class="triage-section">
    <div class="section-label">Try a single ticket &#8594; POST /triage</div>
    <div class="triage-form">
      <textarea id="triageInput">{"ticket_id":"TKT-X","created_at":"2025-06-30T10:00:00Z","channel":"chat","subject":"I was charged twice","body":"I see two $29.99 charges this month.","account":{"user_id":"USR-X","email":"test@example.com","subscription_tier":"pro","account_status":"active","recent_payments":[{"date":"2025-06-01","amount":29.99,"status":"success"},{"date":"2025-06-03","amount":29.99,"status":"success"}],"last_login_at":"2025-06-30T09:00:00Z","last_login_country":"US"}}</textarea>
      <div class="form-row">
        <button class="triage-btn" id="triageBtn" onclick="triageOne()">Triage this ticket &#8594;</button>
        <span id="triageStatus" style="font-family:var(--font-mono);font-size:12px;color:var(--ink-faint)"></span>
      </div>
      <div id="triageResult" class="triage-result"></div>
    </div>
  </div>

</main>

<footer>
  <div>
    <div class="footer-wordmark">Sridhar Vanka</div>
    <div class="footer-copy">Writing, building, thinking.</div>
  </div>
  <div class="footer-links">
    <a href="/docs">API docs</a>
    <a href="https://github.com/sridharvanka">GitHub &#8599;</a>
    <a href="https://sridharvanka.me">Portfolio &#8599;</a>
  </div>
</footer>

</div><!-- /site-wrap -->

<script>
const TERMINAL_AGENTS = new Set(["HumanEscalationAgent", "OutageAgent", "FraudDetectionAgent"]);

function agentClass(name) {
  if (name === "FraudDetectionAgent") return "fraud";
  if (TERMINAL_AGENTS.has(name)) return "terminal";
  return "";
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function renderCard(t) {
  const intents = (t.intents || []).map(function(i) {
    return '<span class="intent-tag">' + escHtml(i) + '</span>';
  }).join("");
  const chain = (t.agents_invoked || []).map(function(a, i) {
    const cls = agentClass(a);
    const arrow = i < t.agents_invoked.length - 1 ? '<span class="chain-arrow">&#8594;</span>' : "";
    return '<span class="agent-chip ' + cls + '">' + escHtml(a.replace("Agent","")) + '</span>' + arrow;
  }).join("");
  const badgeCls = "badge-" + t.resolution;
  const notes = (t.notes || "").split(" | ").map(function(n){ return "  " + n; }).join("\n");
  return '<div class="ticket-card">'
    + '<div class="ticket-top">'
    + '<div>'
    + '<div class="ticket-id">' + escHtml(t.ticket_id) + ' &nbsp;&middot;&nbsp; ' + (t.steps||0) + ' step' + (t.steps!==1?"s":"") + '</div>'
    + '<div class="ticket-subject">' + escHtml(t.subject || "") + '</div>'
    + '</div>'
    + '<span class="resolution-badge ' + badgeCls + '">' + escHtml(t.resolution.replace("_"," ")) + '</span>'
    + '</div>'
    + '<div class="ticket-meta">' + intents + '<span class="confidence">confidence ' + (((t.confidence||0)*100).toFixed(0)) + '%</span></div>'
    + '<div class="agent-chain">' + chain + '</div>'
    + '<div class="ticket-notes">' + escHtml(notes) + '</div>'
    + '</div>';
}

async function loadQueue() {
  try {
    const res = await fetch("/tickets");
    const tickets = await res.json();
    document.getElementById("queueHeader").textContent =
      tickets.length + " synthetic tickets queued — click Run pipeline to process";
    document.getElementById("queueList").innerHTML = tickets.map(function(t) {
      return '<div class="queue-row">'
        + '<span class="queue-id">' + escHtml(t.ticket_id) + '</span>'
        + '<span class="queue-channel">' + escHtml(t.channel) + '</span>'
        + '<span class="queue-subject">' + escHtml(t.subject) + '</span>'
        + '</div>';
    }).join("");
  } catch(e) {
    document.getElementById("queueHeader").textContent = "9 synthetic tickets queued";
  }
}

document.addEventListener("DOMContentLoaded", loadQueue);

async function runAll() {
  const btn = document.getElementById("runBtn");
  const txt = document.getElementById("runBtnText");
  btn.disabled = true;
  txt.innerHTML = '<span class="spinner"></span>&nbsp; Running&hellip;';
  document.getElementById("results").style.display = "none";

  try {
    const res = await fetch("/run");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Pipeline error");
    const tickets = data.tickets || [];
    const resolved = tickets.filter(function(t){ return t.resolution==="resolved"; }).length;
    const escalated = tickets.filter(function(t){ return t.resolution==="escalated"; }).length;
    const fraud = tickets.filter(function(t){ return t.resolution==="fraud_flagged"; }).length;
    document.getElementById("resultsHeader").textContent =
      tickets.length + " tickets processed  ·  " + resolved + " resolved  ·  " + escalated + " escalated  ·  " + fraud + " fraud flagged";
    document.getElementById("ticketCards").innerHTML = tickets.map(renderCard).join("");
    document.getElementById("queueSection").style.display = "none";
    document.getElementById("results").style.display = "block";
  } catch(e) {
    document.getElementById("ticketCards").innerHTML = '<div class="error-msg">Error: ' + escHtml(e.message) + '</div>';
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
    const res = await fetch("/triage", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: input
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Error");
    document.getElementById("triageResult").innerHTML = renderCard(data);
    status.textContent = "";
  } catch(e) {
    document.getElementById("triageResult").innerHTML = '<div class="error-msg">Error: ' + escHtml(e.message) + '</div>';
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
