# cmndr MVP — Build Strategy & Design

**Date:** 2026-06-29
**Status:** draft (pending approval)
**Companion spec:** `cmndr-mvp-technical-requirements.md` (the architecture + requirements doc — source of truth for *what* the system is; TR-1…TR-32, §1–§15). This design covers *how we build it* given the existing repo, and pins the frontend surfaces.

---

## 1. Scope

Build the **entire cmndr MVP** as specified in the requirements doc, in one sequenced plan (not separate sub-specs). The plan is phased so it stays buildable and reviewable, and so subagents can work independent tasks in parallel.

In scope: the three-zone device pipeline (Router, Anonymizer, Restorer, Preview, Audit, local backend, Entry Point), the CGN GPU network (Registry, Orchestrator/Dispatcher, node worker, challenge/redundancy + reputation), node registration (operator accounts, single-use enrollment tokens, node-side keypairs, canary), and the two frontend surfaces.

Out of scope (per §2 / §4 non-goals): medical/legal verticals, real-time inference, payment/incentive settlement, sybil-proof open registration, per-job cryptographic proof of inference.

## 2. Existing-code salvage strategy

The repo already holds ~1,480 LOC built against the older `plan.md` (credit-ledger GPU network, push dispatch, challenge/expected-answer verification). Decision per component:

| Component | Verdict | Action |
|---|---|---|
| `anonymizer/pipeline.py` | **Salvage (~60%)** | Keep spaCy+Presidio detection, typed placeholders, identical-value reuse. Refactor: add `detect(text)→entities` seam (§6.3); restructure the flat map into `RedactionMap` entries `{placeholder, original_value, entity_type, span, source}` so preview edits have a home (M3); make `restore` flag dropped/paraphrased placeholders as `restoration_anomalies` instead of silent `.replace()` (TR-3, §6.5). |
| `node_agent/` (vLLM) | **Salvage inference core** | Reuse the inference call. Rebuild the network shell: local keypair gen, enrollment, **outbound persistent connection** (CD1), result **signing** (TR-17), canary response (TR-24). |
| `orchestrator/` (main/registry/scheduler/verifier) | **Rebuild semantics, keep scaffolding (~30–40%)** | Keep the FastAPI+SQLite skeleton, registry table, heartbeat, job-queue + dispatch-loop. **Delete** the credit ledger (CD5 non-goal). **Replace** challenge/`is_challenge`+expected-answer with **redundant dispatch + signed results + reputation** (TR-17/18/19). **Replace** open `node_id` registration with keypair + enrollment-token + operator-account (§8). **Invert** push→pull dispatch (CD1/CD2). |
| `client/cmndr.py` | **Reference only** | Becomes a smoke test. The real Zone-1 device pipeline (Router, preview, audit, signature-verify-before-restore, boundary enforcement) is new. |

Net: the salvageable code lives at the *edges* (anonymization NLP, node inference). The value-defining spine — trust zones, Router, preview, audit, signed-results+reputation, keypair/token registration, status stream, frontends — is greenfield.

## 3. Frontend surfaces (designed in this session)

### 3.1 Customer console (trusted side) — the "decision tree"
A trusted-device console (§9.1, TR-26/27/29/31/32). The user types a prompt; the pipeline is rendered as a **top-down decision tree** that mirrors the product's hand-drawn flow:

- Input → **Router** (the decision node; a side panel shows its reasoning: task type, sensitivity, confidence vs. threshold → LOCAL or ESCALATE).
- The **taken** path lights up; the untaken branch is dimmed.
- Escalate path: **Anonymize** (placeholders shown) → **Preview/edit** (the accept gate — escalation structurally impossible without it, TR-6/TR-31) → **fan-out to cloud LLM nodes** → **Restore (decrypt)** → **Output** (rendered trusted-side only, TR-32).
- A **review loop** edge returns to the input for edits.
- Note: what crosses the boundary is **placeholdered (de-identified) text, not encrypted ciphertext** — the cloud must be able to read it to run inference. The on-device restoration map is what's protected. (Confirmed: not homomorphic/TEE encryption.)

### 3.2 Operator frontend (PII-blind side) — a real app, not a diagram
The GPU/operator side is the actual app (§8.3, §9.2), three screens:
- **Node list** (`/nodes`): table of the operator's GPUs — status (Online / Pending canary / Offline), model, reputation bar, jobs, last seen. **+ Add node** opens a drawer with the one-time `cmndr-node enroll --token=…` command (single-use, time-limited); the node mints its own key and appears once it passes the canary.
- **Single-node detail** (`/nodes/:id`): stats (jobs, latency, uptime, reputation), reputation trend, and a **live job feed** where every row's payload is locked as **🔒 de-identified payload** — the operator-side expression of the boundary invariant (TR-30/32).
- Account/consent + empty first-run states (TR-21) — to be detailed in the plan.

Both surfaces subscribe to the **metadata-only status stream** (`{job_id, state, pct, node_id, elapsed_ms, stage}`); the schema check rejecting any content field is enforced in code (TR-28).

## 4. Build sequencing (one plan, phased)

Per §13 — confidentiality machinery always precedes the untrusted edge.

1. **Spine** — Entry Point + Backend interface + local backend + data model (TR-9).
2. **Anonymization sandwich** — Anonymizer (salvaged) + RedactionMap + Restorer + Provider interface; forced-escalate round-trips (TR-3/4/7).
3. **Preview + invariant** — pre-flight preview + no-unreviewed-escalation enforcement (TR-6).
4. **Router** — local classifier + threshold config + calibration harness (TR-1/2).
5. **Audit + device challenge** — append-only audit log + happy-path challenge (TR-8/10).
6. **CGN A→D** — registry+node → dispatch round-trip → multi-node+resilience → verification+reputation (TR-12…19).
7. **Frontends** — customer console after phase 3; operator dashboard live feed after CGN-B.

The §12.0 shared wire contract (`/enroll`, `/heartbeat`, `Job`, `Result`, `/challenge`, `StatusEvent`) is **frozen first**, before any parallel work.

## 5. Open decisions

All §14 leans accepted as MVP defaults: D1 per-consumer preview (default human review), D2 per-task-type threshold, D3 local append-only audit, CD1 node-outbound connection, CD2 device↔node via orchestrator, CD3 10–20% redundancy sampling, CD5 no payment. No re-decision needed unless changed here.

## 6. How subagents execute this

The plan (next, via `writing-plans`) decomposes phases into independent tasks. Parallelism follows §12's trust-boundary split: a **trusted-side track** (device pipeline + orchestrator/registry/verifier — correctness- and privacy-critical) and an **untrusted-side track** (node worker + operator backend/frontend). Both build against fakes after the §12.0 contract is frozen; joint milestones (enrollment, dispatch, canary, verification) synchronize them. A1/A3/A4/A5 (privacy-critical) get genuine review regardless.

## 7. Testing

TDD throughout. Each TR has an acceptance criterion that becomes a test. Boundary-invariant tests (TR-6/7/15/16) and the metadata-only status-stream schema check (TR-28) are the load-bearing ones — they must fail loudly when violated.
