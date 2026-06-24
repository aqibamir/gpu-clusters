# Decentralized Inference Network — MVP Architecture Plan

**Scope:** Apple Silicon nodes · Whole-model-per-node only · No blockchain · No ZKP/TEE  
**Goal:** End-to-end batch job completes across 3+ real Macs before any further scaling  
**Date:** 2026-06-24

---

## What's in the MVP

| In | Out (deferred) |
|---|---|
| Apple Silicon node agent (llama.cpp + Metal) | NVIDIA / CUDA nodes |
| Whole-model-per-node scheduling | Layer-splitting (Petals-style) |
| Central orchestrator (one process) | Distributed P2P orchestration |
| Basic anonymization (NER + placeholders) | Full PII coverage, differential privacy |
| Challenge-request verification (5%) | ZKP / TEE |
| SQLite credit ledger | Staking, tokens, blockchain |
| REST/HTTP transport | gRPC, encrypted tunnels |
| Batch jobs only (cron / document sets) | Interactive chat, sub-second inference |

The MVP is a working proof of the privacy-first batch inference loop on real hardware. Everything else comes after that's proven.

---

## System Overview

```
┌─────────────────────────────────────────────────────────┐
│  SME Device (on-premise)                                │
│                                                          │
│  ┌──────────────┐    ┌───────────────────────────────┐  │
│  │  cmndr / job │───▶│  Anonymization Layer          │  │
│  │  submitter   │    │  • NER entity extraction       │  │
│  │              │◀───│  • Placeholder substitution    │  │
│  └──────────────┘    │  • Restoration map (local)     │  │
│                      └──────────────┬────────────────┘  │
└─────────────────────────────────────┼───────────────────┘
                                      │ anonymized job
                                      ▼
┌─────────────────────────────────────────────────────────┐
│  Orchestrator (cloud VM or dedicated server)             │
│                                                          │
│  ┌───────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Job Queue │  │  Scheduler   │  │  Node Registry   │  │
│  │ (SQLite)  │  │  (capability │  │  (capabilities,  │  │
│  │           │  │   matcher)   │  │   heartbeats)    │  │
│  └─────┬─────┘  └──────┬───────┘  └──────────────────┘  │
│        │               │                                  │
│  ┌─────▼───────────────▼──────────────────────────────┐  │
│  │  Verification Engine                                │  │
│  │  • 5% challenge-request injection                   │  │
│  │  • Result validation                                │  │
│  │  • Credit ledger (SQLite)                           │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────┘
                           │ anonymized job dispatch
              ┌────────────┼────────────┐
              ▼            ▼            ▼
      ┌───────────┐ ┌───────────┐ ┌───────────┐
      │ Node A    │ │ Node B    │ │ Node C    │
      │ Mac M3    │ │ Mac M2    │ │ Mac M4    │
      │ llama.cpp │ │ llama.cpp │ │ llama.cpp │
      │ Metal     │ │ Metal     │ │ Metal     │
      └───────────┘ └───────────┘ └───────────┘
```

---

## Component Specs

### 1. Node Agent

Runs on every contributor Mac. Single Python process.

**Responsibilities:**
- Load and hold a quantized model in unified memory
- Accept jobs from the orchestrator over HTTP
- Run inference via llama.cpp (Metal backend)
- Report metrics (memory used, tokens generated, latency)
- Send heartbeats every 30s to stay in the active registry
- Receive and execute challenge-requests identically to real jobs

**Key libraries:**
```
llama-cpp-python[metal]   # Metal-accelerated inference
fastapi + uvicorn         # Job receiver HTTP server
httpx                     # Heartbeat + result posting
psutil                    # Memory / CPU metrics
```

**Node capabilities payload (sent on registration):**
```json
{
  "node_id": "uuid",
  "os": "darwin",
  "chip": "apple_m3_pro",
  "ram_gb": 36,
  "max_model_size": "70b-q4",
  "throughput_class": "medium",
  "models_loaded": ["mistral-7b-q4_k_m"]
}
```

**Job contract (what a node receives):**
```json
{
  "job_id": "uuid",
  "model": "mistral-7b-q4_k_m",
  "prompt": "Summarize the following: [PERSON_1] submitted [DOC_1]...",
  "max_tokens": 512,
  "is_challenge": false
}
```

---

### 2. Orchestrator

Single FastAPI process. Runs on a small cloud VM (or any always-on machine) in the MVP.

**Responsibilities:**
- Maintain the node registry (online nodes, capabilities, last heartbeat)
- Accept jobs from the Job API
- Schedule: match each job to the cheapest capable node currently online
- Inject challenge-requests (~5% of traffic, indistinguishable from real jobs)
- Track results, compare challenge answers, update node reputation scores
- Maintain the credit ledger

**Scheduler logic (MVP — simple capability match):**
```python
def pick_node(job: Job, registry: NodeRegistry) -> Node:
    candidates = [
        n for n in registry.online_nodes()
        if n.can_run(job.model)           # model fits in RAM
        and n.reputation_score >= 0.8     # not flagged
    ]
    # Prefer nodes already holding the model (no load time)
    loaded = [n for n in candidates if job.model in n.models_loaded]
    pool = loaded if loaded else candidates
    return min(pool, key=lambda n: n.queue_depth)
```

**Database schema (SQLite, 4 tables):**

```sql
-- Registered nodes
CREATE TABLE nodes (
    node_id TEXT PRIMARY KEY,
    chip TEXT, ram_gb INTEGER,
    max_model TEXT, throughput_class TEXT,
    reputation REAL DEFAULT 1.0,
    last_heartbeat INTEGER
);

-- Job queue
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT,           -- queued | dispatched | done | failed
    model TEXT,
    prompt TEXT,
    result TEXT,
    assigned_node TEXT,
    submitted_at INTEGER,
    completed_at INTEGER
);

-- Challenge-requests (hidden test jobs)
CREATE TABLE challenges (
    challenge_id TEXT PRIMARY KEY,
    job_id TEXT,           -- the disguised job ID
    expected_answer TEXT,
    node_id TEXT,
    passed INTEGER         -- 0 or 1
);

-- Credit ledger
CREATE TABLE credits (
    node_id TEXT,
    job_id TEXT,
    tokens_generated INTEGER,
    latency_ms INTEGER,
    credit_amount REAL,
    awarded_at INTEGER
);
```

---

### 3. Anonymization Layer

Runs **on the SME device**, never in the network. The restoration map never leaves the device.

**Pipeline:**

```
Raw prompt
    │
    ▼
┌─────────────────────────────────────┐
│  Entity Extraction (spaCy / Presidio)│
│  Finds: PERSON, ORG, DATE, MONEY,   │
│         EMAIL, PHONE, LOCATION      │
└───────────────────┬─────────────────┘
                    │
                    ▼
┌─────────────────────────────────────┐
│  Placeholder Assignment             │
│  "Acme Corp" → [ORG_1]             │
│  "Jane Smith" → [PERSON_1]         │
│  "€42,000"   → [AMOUNT_1]         │
└───────────────────┬─────────────────┘
                    │
                    ▼
┌─────────────────────────────────────┐
│  Restoration Map (local, encrypted) │
│  {                                  │
│    "ORG_1": "Acme Corp",           │
│    "PERSON_1": "Jane Smith",        │
│    "AMOUNT_1": "€42,000"           │
│  }                                  │
└───────────────────┬─────────────────┘
                    │
                    ▼
         Anonymized prompt → network
```

**On result return:**  
Scan the result for all placeholder tokens, replace each with its restored value from the map. Map is discarded after restoration.

**Key libraries:**
```
spacy + en_core_web_lg    # NER (offline, no API calls)
presidio-analyzer         # Extra PII patterns (email, phone, etc.)
cryptography              # Fernet encryption of the restoration map
```

**MVP limitation to state explicitly:** Spacy NER misses ~10–20% of entities and creates false positives. This is acceptable for MVP batch summarization; it is not acceptable for medical or legal data. Flag for post-MVP.

---

### 4. Job API

The external interface — what cmndr (or any client) calls to submit and retrieve work.

**Endpoints (REST):**

```
POST /jobs
  Body: { model, raw_prompt, max_tokens, priority }
  → Anonymizes on-device, submits anonymized job to orchestrator
  ← { job_id, status: "queued" }

GET  /jobs/{job_id}
  ← { job_id, status, result (restored), submitted_at, completed_at }

GET  /jobs/{job_id}/status
  ← { status, assigned_node (opaque ID), progress }

POST /nodes/register
  Body: node capabilities JSON
  ← { node_id, registered: true }

POST /nodes/{node_id}/heartbeat
  ← { accepted: true }

POST /nodes/{node_id}/result
  Body: { job_id, output, tokens_generated, latency_ms }
  ← { credited: true, credit_amount }
```

---

## Tech Stack Summary

| Layer | Choice | Why |
|---|---|---|
| Node inference | llama.cpp via `llama-cpp-python[metal]` | Metal-accelerated, runs quantized models on unified memory, active community |
| Node HTTP server | FastAPI + uvicorn | Lightweight, async, easy to add auth later |
| Orchestrator | FastAPI + SQLite | Simple enough to debug on day 1; swap Postgres later |
| Anonymization | spaCy + Presidio | Both run fully offline, no API calls, enterprise-grade PII coverage |
| Transport | HTTP/REST (HTTPS in prod) | Debuggable, observable, no special client needed |
| Packaging | uv + pyproject.toml | Fast, reproducible, easy node install story |

---

## Build Sequence (5 Phases)

### Phase 1 — Local inference loop (1–2 weeks)
**Goal:** A single Mac runs a job end-to-end, no network involved.

- [ ] Install llama.cpp + Metal backend, confirm model loads
- [ ] Write `node_agent.py`: loads model, accepts a JSON job, returns result
- [ ] Test with `mistral-7b-q4_k_m` (fits in 8 GB, fast, well-tested)
- [ ] Measure: time-to-first-token, tokens/sec, memory headroom

**Exit criterion:** A curl command submits a job and gets a coherent response back.

---

### Phase 2 — Orchestrator + 2 nodes (2–3 weeks)
**Goal:** Orchestrator dispatches jobs to 2 real Macs over LAN/VPN.

- [ ] Build `orchestrator.py`: node registry, job queue, simple scheduler
- [ ] Node heartbeat loop (every 30s)
- [ ] Scheduler: pick node → dispatch → collect result
- [ ] SQLite schema: nodes, jobs tables
- [ ] Run on 2 Macs on the same network

**Exit criterion:** Submit a job to the orchestrator; it runs on whichever Mac is available.

---

### Phase 3 — Anonymization layer (1–2 weeks)
**Goal:** Prompts are anonymized before leaving the device; results are restored on return.

- [ ] Set up spaCy + Presidio pipeline
- [ ] Placeholder assignment + restoration map
- [ ] Fernet encryption of the map at rest
- [ ] Restoration on result return
- [ ] Accuracy audit: 50 sample prompts, measure missed entities

**Exit criterion:** A prompt containing real names/orgs reaches the node with only placeholders; result returned to submitter has real values restored.

---

### Phase 4 — Verification + credits (1–2 weeks)
**Goal:** Nodes can't farm credits by returning garbage.

- [ ] Challenge-request pool: 20+ test prompts with known correct answers
- [ ] Inject into job stream at ~5% rate, indistinguishably from real jobs
- [ ] Semantic comparison: use a local model to judge if node answer matches expected answer
- [ ] Reputation scoring: node drops below 0.8 → throttled
- [ ] Credit ledger: award credits per tokens generated, discounted by latency

**Exit criterion:** A node returning random outputs fails challenges and stops receiving jobs.

---

### Phase 5 — Real-world stress test (1–2 weeks)
**Goal:** 3+ Macs, real batch workload, end-to-end.

- [ ] Run a realistic batch: 100 document summarizations
- [ ] Introduce deliberate node churn (kill a node mid-batch) — confirm scheduler recovers
- [ ] Measure: total throughput, latency per job, credit distribution
- [ ] Audit anonymization on real documents
- [ ] Document everything that broke

**Exit criterion:** 100 jobs complete with no data leakage and no stuck queue, across 3+ machines.

---

## Key Risks to Watch

| Risk | Mitigation |
|---|---|
| llama.cpp Metal performance varies by chip | Benchmark M2, M3, M4 early; set realistic throughput tiers |
| Node churn mid-job | Orchestrator marks job failed → requeues; don't bill failed jobs |
| Anonymization misses entity | Log all missed-entity incidents; add Presidio custom recognizers for domain-specific terms |
| Challenge semantic comparison is fuzzy | Use strict exact-match for challenge answers in MVP (controlled test set); fuzzy matching post-MVP only |
| SQLite contention under load | One writer at a time is fine for MVP scale; Postgres when >10 nodes |
| Network reliability (home/office internet) | Timeout after 2× expected job duration; requeue |

---

## First Thing to Build

Start with Phase 1, specifically this:

```bash
pip install llama-cpp-python[metal] fastapi uvicorn httpx
```

Then a `node_agent.py` that:
1. Loads `mistral-7b-q4_k_m.gguf` at startup
2. Exposes `POST /run` that accepts `{ job_id, prompt, max_tokens }`
3. Returns `{ job_id, output, tokens_generated, latency_ms }`

Nothing else. Get that working first on one Mac. Everything else is built on top of proven local inference.

---

## What This MVP Proves

1. A batch job can be anonymized, sent to a contributor Mac, run with llama.cpp on Metal, and returned with entities restored — **without the node ever seeing real data**.
2. The verification layer makes it irrational (not just illegal) for nodes to return garbage.
3. The scheduling loop handles node availability dynamically.

Layer-splitting, NVIDIA, ZKP, staking — none of that is needed to prove the core value proposition. Prove the loop first.