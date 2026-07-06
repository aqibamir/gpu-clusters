---
type: spec
status: draft
version: 0.1
priority: now
created: 2026-06-29
last_updated: 2026-06-29
created_by: agent:claude
updated_by: agent:claude
tags: [spec, cmndr, mvp, architecture, requirements, moa, anonymization, gpu-network, private-inference]
vault: "la Chaine"
audience: [engineering, stakeholders]
scope: mvp
related:
  - "[[cmndr]]"
  - "[[projects/private-inference-network]]"
  - "[[sources/gpu-clusters-plan]]"
---

# cmndr MVP — Technical Requirements & Architecture

> **Scope.** This document specifies the cmndr MVP: an on-device compliance pipeline
> (MoA router + anonymization sandwich) plus an untrusted, third-party GPU inference
> network that serves as the escalation tier. It is written for two audiences —
> engineering (testable requirements, interfaces, acceptance criteria) and
> stakeholders (vision, honest limits). The full product vision is preserved as
> context, but only the MVP is specified for build.

---

## 1. Executive summary (plain language)

cmndr is a compliance layer that sits *above* model providers. For each request, it
decides whether the customer's own machine can answer it or whether it needs outside
help. If it needs outside help, it first hides every piece of personal information in
the text, so the customer's real data never leaves their machine. The hidden-PII text
is then sent to a network of third-party GPUs to be processed; the answer comes back
still containing the placeholders, and the customer's machine swaps the real details
back in before showing them a normal-looking answer.

The system has three zones separated by one hard rule:

- **Zone 1 — the trusted device.** The only place real PII ever lives. Routing,
  anonymization, the human approval step, and restoration all happen here.
- **The privacy boundary.** Only text that has already had its PII hidden may cross
  this line. Nothing else has a path across it.
- **Zone 2 — coordination infrastructure (you operate it).** Matches jobs to GPU
  workers. PII-blind by construction — it only ever handles already-hidden text.
- **Zone 3 — third-party GPU nodes (untrusted strangers).** Anyone can register a GPU
  and earn by processing jobs. They only ever see hidden-PII text.

**The core invariant, stated once:** real customer PII only exists in Zone 1. Every
other component — orchestrator, registry, nodes, operator frontend, status stream —
is designed never to see it.

---

## 2. Honest limits (read before anything else)

Three limits are deliberate and must be communicated to stakeholders, not discovered
late.

1. **Anonymization is ~80–90% complete, not perfect.** The MVP detector (spaCy NER +
   Presidio) misses an estimated 10–20% of entities and produces some false positives.
   This is *acceptable for batch summarization* precisely because a human approval step
   (the pre-flight preview) catches misses. It is *not acceptable* for medical or legal
   data unattended. The MVP does not serve regulated verticals; closing this gap is
   post-MVP work.

2. **Confidentiality, not integrity.** cmndr guarantees that PII stays confidential. It
   does **not** guarantee that cloud/node outputs are correct. Stakeholders tend to
   assume an inference layer guarantees output quality — it does not.

3. **Node honesty is statistically enforced, not proven per job.** With untrusted
   third-party nodes you cannot cheaply prove a given GPU faithfully ran your model on
   every job. The MVP catches consistent bad actors (redundant sampling + signed,
   attributable results + reputation) but does not provide per-job cryptographic proof
   of correct inference. If a use case needs that, the answer is trusted nodes or TEEs,
   not this MVP.

---

## 3. Product vision (context only — not all MVP)

These describe cmndr fully realized. The MVP commitments are in Section 4.

- **V1 — Routing (MoA).** Per-request decision to answer locally or escalate, with
  tiered escalation, calibrated against representative SME workloads (measured, not
  assumed).
- **V2 — Anonymization sandwich.** Detect → placeholder → infer → restore before any
  boundary crossing; real data never leaves the device.
- **V3 — Transparent pre-flight preview.** Show exactly what will be sent, entities
  marked, editable before it goes out.
- **V4 — Compliance boundary.** Explicit, auditable rule for what may leave the device,
  with residency guarantees; every escalation logged and traceable.
- **V5 — Trust & verification.** A consumer can independently verify PII was stripped
  before remote inference, rather than trusting the layer blindly.
- **V6 — Integration surface.** A stable interface so agents, cron jobs, and other
  products consume compliant inference without knowing the internals.
- **V7 — Portability.** Routing and anonymization are independent of any specific
  hardware backend; new compute targets plug in behind a common interface.
- **V8 — Regulated-vertical readiness.** PII coverage strong enough for medical/legal
  data (the eventual destination).

---

## 4. MVP requirements

Each notes how it stays extensible to other machines.

- **M1 — Local-first batch routing.** Per request, decide local vs. escalate.
  *Extensible:* the decision interface is backend-agnostic.
- **M2 — Anonymization sandwich, end-to-end.** Detect → placeholder → infer → restore,
  on-device, via spaCy NER + Presidio, with a reversible on-device map. *Ceiling:*
  ~80–90% coverage (see Section 2).
- **M3 — Editable pre-flight preview (non-negotiable).** User sees and can edit the
  redacted payload before escalation. This is the control that makes the M2 ceiling
  acceptable.
- **M4 — Escalation audit log.** Every escalation records what was sent (placeholdered
  form), when, and to which target — sufficient to demonstrate PII never left in
  cleartext.
- **M5 — Challenge-request verification.** The orchestrator can verify the sandwich
  actually stripped PII; for untrusted nodes this becomes load-bearing.
- **M6 — Apple Silicon batch backend.** Privacy-first batch inference on Apple Silicon.
  *Extensible:* it is *a* backend behind the M1/V7 interface, not *the* backend.
- **M7 — Single integration entry point.** One clean interface a workspace agent or
  cron job calls; full platform/AWP-Connect wiring intentionally uncommitted.

**MVP non-goals:** medical/legal verticals; real-time/low-latency inference; full
decentralized economic network (incentives/payment); sybil-proof open registration.

---

## 5. Architecture

### 5.1 The three zones

```
                    ON-DEVICE — Zone 1 (trusted, sees real data)
   ┌───────────────────────────────────────────────────────────────────┐
   │  [Entry Point / Console] → [Router] → [Anonymizer] → [Preview]      │
   │                                            │ map held here          │
   │                                            ▼                        │
   │                                   ═══ PRIVACY BOUNDARY ═══──────────┼──┐
   │                                   only placeholdered text crosses   │  │
   │  [Restorer] ◄──────────────────────────────────────────────────────┼──┤
   │  [Audit log]   [Apple Silicon backend]   [Challenge verifier]       │  │
   └───────────────────────────────────────────────────────────────────┘  │
                                                                           │
                    Zone 2 — coordination (you run it, PII-blind)          │
   ┌───────────────────────────────────────────────────────────────────┐  │
   │  [Orchestrator]            [Node Registry]                         │◄─┘
   │  matches jobs → nodes      who's online, what they can do          │
   └───────────────────────────────────────────────────────────────────┘
                                     │ placeholdered job
                                     ▼
                    Zone 3 — GPU nodes (untrusted third parties)
   ┌───────────────────────────────────────────────────────────────────┐
   │  [GPU node]   [GPU node]   [GPU node]   ... only see hidden-PII text │
   └───────────────────────────────────────────────────────────────────┘
```

### 5.2 Request lifecycle

A request enters through the Entry Point (or the trusted-side console). The Router
classifies it local vs. escalate. Local requests go straight to the on-device backend.
Escalating requests enter the anonymization sandwich: the Anonymizer detects PII and
rewrites the payload into placeholdered text, holding a reversible map on-device. The
Pre-flight preview surfaces that payload (entities marked) for inspection/edit; only
after acceptance does the request cross the boundary. The Orchestrator dispatches the
placeholdered job to a capable, healthy node; the node runs inference and returns a
signed, still-placeholdered result. The Restorer reverses the map on-device, putting
real values back. Local and restored responses converge and return. Every routing and
escalation decision writes to the Audit log; the Challenge verifier can confirm the
sandwich worked.

**Invariant:** the only data permitted to cross the device→network boundary is
post-anonymization, post-preview placeholdered text.

### 5.3 Extensibility seams

Three interfaces are defined abstractly, implemented once for the MVP:

- **Backend interface** — `infer(prompt) → completion`. The Apple Silicon loop is one
  implementation; another machine is another implementation. No backend-specific type
  leaks upward.
- **Provider/Network interface** — the escalation target. The GPU network slots in
  exactly where a single cloud provider would; Router and sandwich don't know which.
- **Entry Point interface** — what agents, cron jobs, and the console call. Platform
  wiring lives behind it and is uncommitted by design.

If these three contracts hold, porting to new compute is "write a new implementation,"
not "rework the pipeline."

---

## 6. Component specifications

### 6.1 Entry Point (M7)
Single interface accepting a batch of requests, returning a batch of responses; the
only thing platform/agents/console touch. Synchronous-batch for MVP (no streaming).
Stable across internal changes.

### 6.2 Router (M1)
Classifies each item `local` or `escalate` using a **small local classifier model**.
Features: task type, payload characteristics (length, complexity), sensitivity hint.
Output: label + confidence. **Decision rule:** `local` with confidence ≥ threshold →
local; otherwise → escalate. Erring toward escalation is safe because the sandwich
protects escalated traffic. Threshold is configurable (per-task-type recommended), not
hardcoded — it is the calibration knob.

### 6.3 Anonymizer (M2)
Detects PII (spaCy NER + Presidio) and rewrites escalation-bound payloads into
placeholdered text, producing a **reversible redaction map** held on-device for the
request lifetime. Placeholders are opaque, type-tagged, unique within a request
(`⟦PERSON_1⟧`, `⟦EMAIL_1⟧`, `⟦ORG_2⟧`); the same real value maps to the same
placeholder within one request. The map is never serialized anywhere that could cross
the boundary. Detection sits behind a `detect(text) → entities` interface so stronger
detectors drop in later (the V8 path).

### 6.4 Pre-flight preview (M3) — non-negotiable
Shows the exact placeholdered payload that will cross the boundary, entities marked,
editable before escalation. The user can add a missed redaction (false negative),
remove a wrong one (false positive), or edit; edits update the map before escalation.
**Escalation must be structurally impossible without a preview-accept step.** For
batch/cron consumers, "preview" may be a batch-review surface or a policy auto-accept
for low-sensitivity tasks — but a no-review escalation path must not exist (see Open
Decision D1).

### 6.5 Restorer (part of M2)
Reverses the redaction map over the (signed, verified) result, substituting real values
back for placeholders. Dropped/paraphrased placeholders are flagged in
`restoration_anomalies`, never silently lost.

### 6.6 Audit log (M4)
Records every routing/escalation decision in reconstructable form. Each entry: request
id, timestamp, route, classifier confidence, (for escalations) placeholdered payload
sent, entity count/types, target node/provider, preview-accepted flag, restoration
anomalies. **Never stores raw PII or the redaction map.** Local append-only store for
MVP.

### 6.7 Apple Silicon backend (M6)
Runs local inference for `local`-routed requests. Implements the Backend interface. It
is *a* backend, not *the* backend — another machine is another implementation.

### 6.8 Orchestrator + Challenge verifier (M5)
Coordinates the batch loop and provides verification. See Section 7 for the network
detail, since with untrusted nodes this becomes the accountability layer rather than a
happy-path check.

---

## 7. GPU network (CGN)

> **Trust model:** untrusted third-party nodes · **Role:** escalation tier (replaces
> the cloud provider) · **Deployment:** real nodes over the internet.

### 7.1 How CGN fits

CGN replaces the single "cloud provider" behind the Provider interface with a pool of
registered GPU nodes plus an orchestrator. **Nothing upstream of the boundary changes** —
Router, Anonymizer, preview, and the boundary invariant are untouched. What changes is
that the far side is now many untrusted machines, so it needs structure a single
accountable provider gave for free: discovery, dispatch, verification, result integrity.

**Design principle:** the orchestrator and registry are PII-blind by construction. They
route jobs and track nodes, but every job is already placeholdered, so even a
compromised orchestrator cannot harvest PII.

### 7.2 Node Registry
Tracks which nodes exist, their capabilities, and availability/health. A node registers
with node id, public key, declared capabilities (models, max context, throughput hint),
and endpoint. Liveness via heartbeat; silent nodes are excluded from dispatch.
Registration is open (the "decentralized" property) but registration ≠ trust — trust is
earned through verification and reputation.

### 7.3 Orchestrator / Dispatcher
Matches a placeholdered job to an available, capable node, sends it, awaits the result,
handles failure. On timeout/drop, **reassigns to a different node** (jobs are idempotent
and node-agnostic — a job carries everything needed to run and holds no node-specific
state). MVP selection: capability-filter → reputation tiebreak → least-loaded.

### 7.4 GPU node (worker)
A long-running agent that registers, heartbeats, accepts placeholdered jobs, runs
inference on its local GPU (reusing the Backend interface wrapped in a network shell),
**signs** results, returns them, and answers challenges. It sees only placeholdered
text — never the map, raw payload, or customer identity. Ships as a runnable agent so
"register a GPU" is "run this binary with your endpoint."

### 7.5 Challenge verifier & reputation (load-bearing)

Three distinct concerns, with honest MVP scope:

- **(a) Privacy verification — "did raw PII cross?"** Guaranteed *structurally*, not by
  challenge: the boundary invariant means only placeholdered text was ever sent,
  verified on-device before dispatch. The node can't receive what was never sent.
- **(b) Result integrity — "did the node really run the model?"** The hard one. MVP
  answer: **redundant dispatch for a sampled fraction of jobs** (send to ≥2 nodes,
  compare; divergence flags a node) plus signed results so a misbehaving node is
  *identifiable* and can be down-reputed/ejected. This catches consistent bad actors;
  it is not per-job cryptographic proof.
- **(c) Result authenticity — "who produced this?"** Every result is signed with the
  node's registered key. Cheap, and it makes (b)'s reputation system possible because
  misbehavior is attributable.

**Reputation:** verification outcomes feed a node score the Dispatcher uses for
selection. Nodes start neutral; passing checks raises score; divergence/timeouts/drops
lower it; below a floor → ejected. This is the accountability substitute for the
contract you don't have with anonymous nodes. A running score, not a blockchain.

---

## 8. Node registration

Registration is **two surfaces**, not one. Conflating them is the usual mistake.

### 8.1 Two surfaces

- **Operator onboarding (frontend).** A human creates an operator account and gets a
  credential. The frontend's job ends at "here's your node, here's how to run it." It
  does **not** create the node's identity.
- **Node enrollment (node software, no human).** The node software generates its own
  keypair locally and uses an enrollment token to register the node's *public* key. The
  private key is generated on the node and **never leaves it**.

The split matters because if the frontend generated keys, the browser and your server
would touch node private keys — a custody and attack-surface problem. Instead the
frontend issues a short-lived, single-use **bootstrap token** whose only job is to prove
"this node belongs to operator X" during first contact. After the handshake the token
is spent and the node stands on its own key forever.

### 8.2 Full flow

1. **Operator signs up (frontend).** Account + node-operator terms/consent.
2. **Operator adds a node (frontend).** Backend generates a one-time, time-limited
   enrollment token bound to the account; frontend shows a token + install command
   (e.g. `cmndr-node enroll --token=XXXX`).
3. **Node generates its identity (node software).** On first run, generates a keypair
   locally; private key written to local storage, never transmitted.
4. **Node enrolls (node → registry).** Presents token, public key, declared
   capabilities, endpoint. Registry validates the token (valid? unexpired? unspent?),
   binds node-public-key ↔ operator-account, marks the token spent, records the node as
   `pending-verification`.
5. **Capability attestation (canary).** Registry/orchestrator sends a small canary job;
   the node must run it on the model it claims to serve. Passing moves it to `eligible`.
6. **Node goes live.** Opens its persistent outbound connection to the orchestrator
   (solves NAT — orchestrator never dials in) and heartbeats. Now a dispatch candidate.
7. **Operator sees status (frontend dashboard).** Online/offline, jobs processed,
   reputation/health.

### 8.3 What the frontend is (and isn't)
It is: an operator account system (sign-up, consent), a node-provisioning page (token +
install command), and a read-only node dashboard. It is **not** where node identity is
created, where jobs flow, or in the data path. It touches accounts and tokens — never
jobs, payloads, or node private keys — keeping it PII-blind and key-blind.

---

## 9. Frontend surfaces — start jobs & monitor

Two surfaces, on opposite sides of the boundary. The decision that keeps this clean:
**monitoring shows metadata only** (progress %, node, timing) — never job content.

### 9.1 Customer console (trusted side)
A new trusted-side surface at the front of Zone 1, upstream of the Router — effectively
a UI over the Entry Point (M7). It lets a cmndr user submit a job (input + task type)
and watch it progress. Job content enters here and never leaves the device
un-anonymized. The console can both start jobs and show live progress (state, percent,
assigned node, timing), and it renders restored output locally (real PII reinstated) —
which only ever happens trusted-side.

### 9.2 Operator dashboard (PII-blind side)
The existing operator surface (registration §8) extended with a live job feed. Shows the
operator their *own* node's activity as metadata only: online status, reputation, jobs
completed, current load, avg time, and a feed of jobs currently on their node (each
marked "de-identified payload"). No job content path is added.

### 9.3 Status stream (the connective tissue)
A metadata-only channel carrying job lifecycle events, subscribed to by both surfaces.
It carries exactly `{ job_id, state, pct, node_id, elapsed_ms, stage }` and nothing
else. **This rule is enforced in code** (reject events with unknown/content fields), not
by convention — it is the new place a leak could hide.

---

## 10. Data model

- **RequestItem** *(Zone 1 only — holds real PII)* — `id`, `task_type`, `payload` (raw),
  `sensitivity_hint?`, `requested_provider?`.
- **RoutingDecision** — `request_id`, `route` (local | escalate), `confidence`,
  `classifier_version`.
- **RedactionMap** *(on-device only — holds real PII; no serialization path across the
  boundary)* — list of `{ placeholder, original_value, entity_type, span, source }`
  where `source` ∈ detector | user-added | user-edited.
- **PlaceholderedPayload** — `request_id`, `text` (placeholdered),
  `entity_summary` (counts + types, no values).
- **Job** *(orchestrator → node)* — `job_id`, `model_spec`, `placeholdered_prompt`,
  `params`, `issued_at`, `timeout`, `job_signature`. No raw data, no map, no customer id.
- **Result** *(node → orchestrator → device)* — `job_id`, `placeholdered_completion`,
  `node_id`, `node_signature`, `completed_at`.
- **EscalationRecord** — `request_id`, `timestamp`, `target`, `placeholdered_payload`,
  `preview_accepted`, `entity_summary`, `restoration_anomalies`.
- **AuditEntry** — persisted union of RoutingDecision + optional EscalationRecord, minus
  anything sensitive (explicitly no `original_value`, no full RedactionMap).
- **StatusEvent** — `job_id`, `state`, `pct`, `node_id`, `elapsed_ms`, `stage`. Metadata
  only.

The two structures holding real PII (`RequestItem.payload`, `RedactionMap`) are exactly
the two with no path to persistence or to the network.

---

## 11. Technical requirements with acceptance criteria

### Core pipeline
- **TR-1 (Router decision).** Classify each item via the local classifier with a
  configurable threshold. *Accept:* decision + confidence for 100% of items; threshold
  changeable via config; below-threshold `local` routes to escalate.
- **TR-2 (Router calibration baseline).** "Coarse but done" = escalate-recall measured
  and reported on a representative SME sample. *Accept:* a calibration report with a
  measured number exists (no target required for MVP, but the number must be known).
- **TR-3 (Anonymization round-trip).** Escalated items detect → placeholder → infer →
  restore correctly. *Accept:* 100% of placeholders in the result are restored;
  dropped/paraphrased ones are flagged, not lost.
- **TR-4 (Placeholder consistency).** Identical values → identical placeholders within a
  request; distinct → distinct. *Accept:* verified on multi-entity payloads.
- **TR-5 (Coverage measurement).** Entity coverage + false-positive rate measured
  against a labeled set. *Accept:* the figures exist. MVP accepts the ceiling; it does
  not accept not knowing it.
- **TR-6 (No-unreviewed-escalation invariant).** No escalation without a preview-accept.
  *Accept:* attempting to escalate without an accepted preview is structurally
  impossible — verified by a test that tries and fails.
- **TR-7 (Boundary invariant).** Only PlaceholderedPayload crosses the boundary.
  *Accept:* the Provider/network call site cannot reach raw payload or RedactionMap; the
  challenge verifier confirms no raw entity appears in escalated traffic.
- **TR-8 (Audit completeness).** Every request → an AuditEntry; every escalation → an
  EscalationRecord; no entry holds a raw value. *Accept:* counts match; no raw value
  present.
- **TR-9 (Backend pluggability).** The Apple Silicon backend implements the Backend
  interface with no Apple-specific type in it. *Accept:* a stub second backend
  substitutes in tests without changing Router, Anonymizer, or Entry Point.
- **TR-10 (Challenge verification, happy path).** On challenge, confirm escalated
  traffic for a request contained none of its original entity values. *Accept:* passes
  for correct anonymization, fails for a deliberately-corrupted case.
- **TR-11 (Confidentiality scope).** No integrity/correctness claim about remote
  outputs. *Accept:* documented in the interface contract and stakeholder material.

### GPU network
- **TR-12 (Node registration).** Register with id, public key, capabilities, endpoint;
  maintain liveness via heartbeat. *Accept:* a heartbeating node is a candidate; a
  silent node is excluded.
- **TR-13 (Capability-matched dispatch).** Only assign a job to a node whose
  capabilities satisfy `model_spec`. *Accept:* an unservable job is queued/failed
  gracefully, never misassigned.
- **TR-14 (Job idempotence & reassignment).** A job is re-runnable on any capable node;
  on failure it reassigns and still completes. *Accept:* killing the assigned node
  mid-job → successful completion elsewhere.
- **TR-15 (Boundary invariant over the network).** Only placeholdered job content
  crosses to nodes. *Accept:* inspection + challenge confirm no raw entity in a Job.
- **TR-16 (Orchestrator/registry PII-blindness).** They never receive raw PII or maps.
  *Accept:* audit shows only placeholdered content + metadata; a simulated orchestrator
  compromise exposes no PII.
- **TR-17 (Result authenticity).** Every Result is signed by the node's registered key
  and verified on-device before restoration. *Accept:* invalid/missing signature →
  rejected and reassigned.
- **TR-18 (Redundant verification sampling).** A configurable fraction of jobs go to ≥2
  nodes and results are compared; divergence flags the node(s). *Accept:* an injected
  misbehaving node produces a divergence flag and a reputation penalty.
- **TR-19 (Reputation-driven ejection).** Nodes below a reputation floor are excluded.
  *Accept:* a node forced below floor stops receiving jobs.
- **TR-20 (Integrity-claim scope).** Document that CGN provides identifiability and
  statistical integrity sampling, not per-job cryptographic proof. *Accept:* stated in
  contract + stakeholder material.

### Registration
- **TR-21 (Operator account & consent).** Create operator accounts and capture terms
  consent before any node can enroll under that account. *Accept:* enrollment under an
  account lacking recorded consent is rejected.
- **TR-22 (Enrollment token).** Tokens are single-use, time-limited, account-bound.
  *Accept:* reused → rejected; expired → rejected; enrolls under exactly its bound
  account.
- **TR-23 (Node-side key generation).** The node generates its keypair locally; the
  private key is never transmitted. *Accept:* enrollment traffic shows only the public
  key leaving the node.
- **TR-24 (Capability attestation).** Declared capabilities are probed with a canary
  before dispatch-eligibility. *Accept:* a node declaring a capability it can't perform
  fails the canary and stays ineligible.
- **TR-25 (Operator dashboard visibility).** Show each operator their nodes' live status
  and health. *Accept:* online/offline reflects within the heartbeat window.

### Frontend surfaces (start jobs & monitor)
- **TR-26 (Customer console — start jobs).** A trusted-device console submits jobs
  (input + task type) to the local Entry Point; input never leaves un-anonymized.
  *Accept:* a console job flows through Router → Anonymizer → preview/escalate as a
  programmatic job does; raw input reaches the Anonymizer but no PII-blind surface.
- **TR-27 (Job lifecycle states).** Each job exposes a defined sequence:
  `routing → anonymizing → preview → dispatched → running → restoring → done` (plus
  `failed`, `reassigned`). *Accept:* each transition emits a status event; the console
  reflects state within one update cycle.
- **TR-28 (Status stream — metadata only).** Carries only
  `job_id, state, pct, node_id, elapsed_ms, stage`; never input/output text, the
  placeholdered payload, or the map. *Accept:* a schema check rejects any event with a
  content field.
- **TR-29 (Customer console — monitor).** Show live per-job state, percent, node,
  timing. *Accept:* a running job advances visibly; a job needing approval surfaces a
  review action; a done job shows its result (rendered trusted-side only).
- **TR-30 (Operator live feed).** Show jobs on the operator's own node as metadata only,
  plus node-level stats. *Accept:* a dispatched job appears as metadata; no content;
  only that operator's node.
- **TR-31 (Preview gate preserved).** Console-initiated jobs create no path that
  bypasses the preview gate. *Accept:* a non-batch console job cannot reach `dispatched`
  without passing `preview`; TR-6 still holds.
- **TR-32 (Result visibility scope).** Restored output is viewable only on the
  trusted-side console, never on a PII-blind surface or in the status stream. *Accept:*
  the dashboard and stream contain no restored output.

### Non-functional
- **N1 — Privacy guarantee.** No customer PII leaves the device in cleartext under any
  escalation path.
- **N2 — Auditability.** Escalation/redaction decisions are logged and reconstructable.
- **N3 — Portability.** Hardware backends are pluggable; no Apple-Silicon assumption
  leaks into routing/anonymization.
- **N4 — Batch performance.** Throughput-oriented for MVP; targets set during
  calibration.
- **N5 — Confidentiality, not integrity.** Guarantees confidentiality; not output
  correctness.

---

## 12. Two-developer work split

The cut runs along the **trust boundary**, so the two halves are balanced and connected
by one stable contract. The natural-looking "you do frontend, I do backend" split is a
trap — the frontend is the smallest surface, so that leaves one developer idle.

### 12.0 The shared contract (agree first, day one)
Both freeze these wire formats before writing anything, committed as a shared schema
file. This file is the seam; changing it is the only thing requiring both to sync.

- `POST /enroll` — `{ enrollment_token, node_public_key, capabilities, endpoint_info }`
  → `{ node_id, status }`.
- `POST /heartbeat` — `{ node_id, signature, current_load }` →
  `{ ack, dispatch_eligible }`.
- **Job** — `{ job_id, model_spec, placeholdered_prompt, params, issued_at, timeout,
  job_signature }`.
- **Result** — `{ job_id, placeholdered_completion, node_id, node_signature,
  completed_at }`.
- `POST /challenge` — `{ challenge_id, canary_prompt, model_spec }` → signed completion.
- **StatusEvent** — `{ job_id, state, pct, node_id, elapsed_ms, stage }`.

### 12.1 Developer A — trusted side (device pipeline + coordination)
Owns the correctness- and privacy-critical half. A bug here is a compliance failure.

- **A1 — Dispatcher (on-device).** New implementation behind the Provider interface;
  timeout + await signed result + request reassignment. Builds against a fake
  orchestrator. *Done:* TR-14.
- **A2 — Restorer integration.** Verify `node_signature` *before* restoring; reject
  unrecognized keys. *Done:* TR-17.
- **A3 — Orchestrator.** Holds the queue, matches jobs to capable nodes, relays
  job/result (device → orchestrator → node, per CD2). No code path that could receive
  raw payload/map. *Done:* TR-13, TR-16.
- **A4 — Node Registry.** Node records, liveness, selection (capability → reputation →
  least-loaded). *Done:* TR-12.
- **A5 — Challenge verifier + reputation.** Redundant sampling, divergence detection,
  reputation score, ejection. Encodes the honesty constraint (statistical, not proof).
  *Done:* TR-18, TR-19.
- **A6 — Audit log.** Append-only, no raw PII/maps. *Done:* TR-8.
- **Plus:** the customer console (TR-26, 27, 29, 31, 32) and the status-stream
  *producer* — A's pipeline knows each job's state.
- **Build order:** A1+A2 (fakes) → A3+A4 → A6 → A5.

### 12.2 Developer B — untrusted side (nodes + operator surfaces)
Owns everything facing the untrusted edge and the human operator. More surfaces, each
more self-contained.

- **B1 — GPU node worker.** Local keypair gen, enroll, heartbeat, run via Backend
  interface, sign results, answer challenges; outbound persistent connection (solves
  NAT). Builds against a fake orchestrator. *Done:* TR-23.
- **B2 — Enrollment handshake (node side).** Pairs with A4. *Done:* TR-22 (node side).
- **B3 — Capability attestation (canary).** Node-side canary response; A4 accepts/
  rejects. *Done:* TR-24.
- **B4 — Operator backend.** Accounts, consent, single-use time-limited tokens. Touches
  accounts/tokens — never jobs/payloads/keys. *Done:* TR-21, TR-22.
- **B5 — Operator frontend.** Sign-up/consent, "add a node" page (token + command),
  read-only node dashboard. *Done:* TR-25.
- **Plus:** the operator live feed (TR-30) consuming the status stream filtered to that
  operator's node.
- **Build order:** B1+B2 (fakes) → B4 → B3 → B5.

### 12.3 Integration points (joint milestones)
1. **Enrollment** (B2 ↔ A4) — a real node enrolls with a real registry.
2. **Dispatch** (A3 ↔ B1) — a real job flows device → orchestrator → node and back.
3. **Canary** (B3 ↔ A4) — registry sends a canary, node answers, eligibility flips.
4. **Verification** (A5 ↔ B1) — redundant jobs to multiple real nodes, divergence
   detection.

Everything else is solo work against fakes. The status stream (TR-28) is shared — it
lives in the §12.0 contract file.

### 12.4 Suggested parallel timeline
Both start at §12.0 together, then diverge. A does A1+A2 while B does B1+B2 (milestone:
enrollment + dispatch real). A does A3+A4+A6 while B does B3+B4 (milestone: canary
end-to-end). A does A5 while B does B5 (milestone: verification/reputation over multiple
real nodes with the dashboard live).

**Asymmetry to watch:** A's work is harder to *verify* (correctness and PII-blindness
are invisible when working, catastrophic when not); B's is more visible. If one
developer is stronger on distributed-systems correctness, they should take A — and A3,
A4, A5 deserve genuine review regardless.

---

## 13. Build sequencing (full system)

Confidentiality machinery always precedes the untrusted edge — there must never be a
state where escalation exists without protection.

1. **Spine.** Entry Point + Backend interface + Apple Silicon backend + data model.
   Batch runs entirely local. (TR-9)
2. **Anonymization sandwich.** Anonymizer + map + Restorer + Provider interface. A
   forced-escalate request round-trips with placeholdering. (TR-3, TR-4, TR-7)
3. **Preview + invariant.** Pre-flight preview + no-unreviewed-escalation enforcement.
   (TR-6)
4. **Router.** Local classifier + threshold config + calibration harness. (TR-1, TR-2)
5. **Audit + verification (device).** Audit log + happy-path challenge. (TR-8, TR-10)

Network layers attach after step 2 (sandwich exists):

- **CGN-A — Registry + one node.** (TR-12)
- **CGN-B — Dispatch round-trip.** Signed result restores on-device. (TR-13, 15, 16, 17)
- **CGN-C — Multi-node + resilience.** Capability filtering, reassignment. (TR-14)
- **CGN-D — Verification + reputation.** Redundant sampling, ejection. (TR-18, TR-19)

Frontend surfaces attach once their backing logic exists: the customer console after
step 3 (so preview is enforced); the operator dashboard live feed after CGN-B (so there
are real jobs to show).

---

## 14. Open decisions

| ID | Decision | Lean |
|----|----------|------|
| D1 | Preview for batch/cron consumers (the real one) | Per-consumer config; default to human review, opt-in auto-accept for low-sensitivity tasks only. Trades against the M2 ceiling. |
| D2 | Router threshold scope | Per-task-type (natural calibration unit). |
| D3 | Audit log storage & retention | Local append-only for MVP; retention TBD with compliance. |
| D4 | Classifier sourcing | Bootstrap with heuristic labels in step 4, measure, improve. |
| D5 | Restoration anomaly handling | Flag + best-effort return for batch summarization; revisit for higher-stakes verticals. |
| CD1 | Transport & NAT | Nodes hold outbound persistent connection to orchestrator; orchestrator never dials in. |
| CD2 | Device ↔ node direct or via orchestrator | Via orchestrator for MVP (device endpoint stays private; orchestrator is PII-blind). |
| CD3 | Redundancy sampling rate | Start ~10–20%, configurable, tune against observed misbehavior. |
| CD4 | Node sybil resistance | MVP accepts the risk with mitigations (rate-limit registrations, require heartbeat history before redundant-verification trust); full staking/identity post-MVP. |
| CD5 | Payment/incentive | Run on recruited nodes without payment for MVP; settlement layer post-MVP. |
| RD1 | Token + command vs. pre-seeded download | Token + command for MVP (technical early operators). |
| RD2 | Identity verification at signup | Verified email + per-account rate-limiting; real identity/staking deferred with incentives. |

---

## 15. Open risks (carried from the brain note)

- **R1 — Router calibration** on representative SME workloads is the real ongoing risk;
  measured, not assumed.
- **R2 — PII coverage** gates which verticals cmndr can serve; closing the gap to V8 is
  unscoped post-MVP.
- **R3 — Vertical/positioning mismatch:** marketing toward regulated verticals while the
  MVP can't safely serve them unattended is a credibility risk if not communicated
  carefully.
- **R4 — Status-stream leak surface (new):** the status stream touches both sides of the
  boundary; its metadata-only rule must be enforced in code, not convention (TR-28).
- **R5 — Sybil/incentive gap (new):** a "decentralized" MVP is in practice a
  "recruited-nodes" MVP until CD4 and CD5 are addressed; name it as deferred so the demo
  is not mistaken for the destination.

---

*End of document.*
