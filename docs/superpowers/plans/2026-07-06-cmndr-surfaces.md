# cmndr Surfaces & Status Stream Implementation Plan (Plan 4 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the two human surfaces on opposite sides of the boundary — operator onboarding + PII-blind dashboard (TR-21/25/30) and the trusted customer console with the full job lifecycle and preview gate (TR-26/27/29/31/32) — connected by the metadata-only status stream (TR-28), then delete the legacy packages.

**Architecture:** Operator side extends `cgn/orchestrator_app.py` (accounts + consent, node list, metadata-only feed derived from Zone-2 state and emitted strictly as `StatusEvent`). Customer side is a new `console/` package: a small FastAPI app on the trusted device that wraps the `Pipeline`, runs each request in a worker thread, parks escalations at the preview gate until approval, and emits `StatusEvent`s for its local stages. Both surfaces are single-file HTML polling JSON — no build step. `Preview` gains `add_redaction` to complete M3.

**Tech Stack:** FastAPI, pydantic `StatusEvent` (frozen in Plan 3), threading, vanilla HTML/JS (no external assets).

## Global Constraints

- Python ≥ 3.11; new package `console/`; tests under `tests/console/` and `tests/cgn/`.
- The status stream carries exactly `job_id, state, pct, node_id, elapsed_ms, stage` — every event MUST be constructed through `cgn.contract.StatusEvent` (extra=forbid) before serialization (TR-28).
- Restored output is served ONLY by the console app's result endpoint on the device; no orchestrator endpoint may return it (TR-32).
- No escalation path may bypass preview-accept (TR-31); console jobs go through the same `Pipeline` as programmatic ones (TR-26).
- Lifecycle states (TR-27): `routing → anonymizing → preview → dispatched → running → restoring → done`, plus `failed`; local route uses `routing → running → done`.
- Every code-bearing task is TDD: failing test → run-fail → minimal impl → run-pass → commit. Run tests with `uv run pytest …`.

---

### Task 1: Operator accounts with consent gate + node list (TR-21 / TR-25 backend)

**Files:**
- Modify: `cgn/orchestrator_app.py`
- Test: `tests/cgn/test_operator_api.py`

**Interfaces:**
- Consumes: existing app factory, `cgn.registry`.
- Produces: `POST /operators` body `{operator_id, email, consent: bool}` → 201 `{operator_id}`; `consent: false` → 400 (no row written; enrollment under that id later fails with `no-consent` via the Task-4 Plan-3 gate). `GET /operators/{operator_id}/nodes` → `{nodes: [{node_id, status, reputation, last_heartbeat, current_load, jobs_done}]}` (jobs_done = COUNT of done assignments for that node). `seed_operator` stays for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_operator_api.py
from fastapi.testclient import TestClient

from cgn.node.identity import NodeIdentity
from cgn.node.worker import NodeWorker
from cmndr.backends.fake import EchoBackend
from cgn.orchestrator_app import create_app


def test_signup_requires_consent():
    client = TestClient(create_app(":memory:"))
    r = client.post("/operators", json={"operator_id": "op1",
                                        "email": "op@example.com", "consent": False})
    assert r.status_code == 400
    r = client.post("/operators", json={"operator_id": "op1",
                                        "email": "op@example.com", "consent": True})
    assert r.status_code == 201


def test_enrollment_under_unconsented_account_rejected(tmp_path):
    app = create_app(":memory:")
    client = TestClient(app)
    # token for an operator that never consented (row absent entirely)
    app.state.conn.execute("INSERT INTO enrollment_tokens VALUES ('t','ghost',0,9e9,0)")
    app.state.conn.commit()
    ident = NodeIdentity.create(tmp_path)
    r = client.post("/enroll", json={
        "enrollment_token": "t", "node_public_key": ident.public_key_hex,
        "capabilities": {"models": ["m"], "max_context": 1, "throughput_hint": "x"},
        "endpoint_info": "o"})
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "no-consent"


def test_operator_sees_own_nodes_with_stats(tmp_path):
    app = create_app(":memory:")
    client = TestClient(app)
    client.post("/operators", json={"operator_id": "op1",
                                    "email": "op@example.com", "consent": True})
    w = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    node_id = w.enroll(token)
    w.heartbeat(); w.poll_once(); w.heartbeat()   # canary → eligible

    nodes = client.get("/operators/op1/nodes").json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == node_id
    assert nodes[0]["status"] == "eligible"
    assert nodes[0]["jobs_done"] == 1             # the canary
    assert "reputation" in nodes[0] and "last_heartbeat" in nodes[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_operator_api.py -v`
Expected: FAIL — `POST /operators` returns 404 (route missing)

- [ ] **Step 3: Add the routes**

In `cgn/orchestrator_app.py`, add below the `JobSubmit` model:

```python
class OperatorSignup(BaseModel):
    operator_id: str
    email: str
    consent: bool
```

and inside `create_app`, after the `issue` route:

```python
    @app.post("/operators", status_code=201)
    def signup(req: OperatorSignup):
        import time
        if not req.consent:
            raise HTTPException(status_code=400,
                                detail={"reason": "consent-required"})
        conn.execute("INSERT OR REPLACE INTO operators VALUES (?,?,?)",
                     (req.operator_id, req.email, time.time()))
        conn.commit()
        return {"operator_id": req.operator_id}

    @app.get("/operators/{operator_id}/nodes")
    def operator_nodes(operator_id: str):
        rows = conn.execute("SELECT * FROM nodes WHERE operator_id=?",
                            (operator_id,)).fetchall()
        nodes = []
        for r in rows:
            done = conn.execute(
                "SELECT COUNT(*) c FROM assignments WHERE node_id=? AND state='done'",
                (r["node_id"],)).fetchone()["c"]
            nodes.append({"node_id": r["node_id"], "status": r["status"],
                          "reputation": r["reputation"],
                          "last_heartbeat": r["last_heartbeat"],
                          "current_load": r["current_load"], "jobs_done": done})
        return {"nodes": nodes}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_operator_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/orchestrator_app.py tests/cgn/test_operator_api.py
git commit -m "feat(cgn): operator signup with consent gate + node stats (TR-21/25)"
```

---

### Task 2: Metadata-only operator feed (TR-28 / TR-30 backend)

**Files:**
- Modify: `cgn/orchestrator_app.py`
- Test: `tests/cgn/test_operator_feed.py`

**Interfaces:**
- Consumes: Zone-2 tables, `cgn.contract.StatusEvent`.
- Produces: `GET /operators/{operator_id}/feed` → `{events: [StatusEvent-dict]}` — one event per assignment on that operator's nodes, newest first. Mapping: assignment `assigned` → state `running`, pct 0.5; `done` → `done`, pct 1.0; `timeout` → `reassigned`, pct 0.0. `elapsed_ms` = (completed_at − assigned_at)·1000 for done, else 0. `stage` = job kind (`inference`|`canary`). Every dict is produced via `StatusEvent(...).model_dump()` so a content field is a `ValidationError`, not a leak.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_operator_feed.py
from fastapi.testclient import TestClient

from cgn.contract import StatusEvent
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app
from cmndr.backends.fake import EchoBackend

SECRET = "⟦PERSON_1⟧ owes rent"


def setup(tmp_path):
    app = create_app(":memory:")
    client = TestClient(app)
    client.post("/operators", json={"operator_id": "op1",
                                    "email": "e@x.com", "consent": True})
    w = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    w.enroll(token)
    w.heartbeat(); w.poll_once(); w.heartbeat()
    return app, client, w


def test_feed_is_metadata_only_and_schema_clean(tmp_path):
    app, client, w = setup(tmp_path)
    client.post("/jobs", json={"model_spec": "echo-model",
                               "placeholdered_prompt": SECRET})
    w.poll_once()
    events = client.get("/operators/op1/feed").json()["events"]
    assert len(events) == 2                     # canary + job
    for ev in events:
        StatusEvent.model_validate(ev)          # TR-28: parses under extra=forbid
        assert SECRET not in str(ev)            # no content, even placeholdered
    done = [e for e in events if e["state"] == "done"]
    assert len(done) == 2
    assert all(e["stage"] in ("inference", "canary") for e in events)


def test_feed_shows_only_that_operators_nodes(tmp_path):
    app, client, w = setup(tmp_path)
    client.post("/operators", json={"operator_id": "op2",
                                    "email": "b@x.com", "consent": True})
    assert client.get("/operators/op2/feed").json()["events"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_operator_feed.py -v`
Expected: FAIL — `/operators/op1/feed` returns 404

- [ ] **Step 3: Add the feed route**

In `create_app`, after `operator_nodes`:

```python
    @app.get("/operators/{operator_id}/feed")
    def operator_feed(operator_id: str):
        from cgn.contract import StatusEvent
        rows = conn.execute(
            "SELECT a.*, j.kind FROM assignments a "
            "JOIN nodes n ON n.node_id = a.node_id "
            "JOIN jobs j ON j.job_id = a.job_id "
            "WHERE n.operator_id=? ORDER BY a.assigned_at DESC LIMIT 100",
            (operator_id,)).fetchall()
        state_map = {"assigned": ("running", 0.5), "done": ("done", 1.0),
                     "timeout": ("reassigned", 0.0)}
        events = []
        for r in rows:
            state, pct = state_map[r["state"]]
            elapsed = int(((r["completed_at"] or r["assigned_at"]) - r["assigned_at"]) * 1000)
            events.append(StatusEvent(job_id=r["job_id"], state=state, pct=pct,
                                      node_id=r["node_id"], elapsed_ms=elapsed,
                                      stage=r["kind"]).model_dump())
        return {"events": events}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_operator_feed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/orchestrator_app.py tests/cgn/test_operator_feed.py
git commit -m "feat(cgn): operator live feed — StatusEvent-enforced metadata only (TR-28/30)"
```

---

### Task 3: Operator dashboard page (TR-25 / TR-30 UI)

**Files:**
- Create: `cgn/static/dashboard.html`
- Modify: `cgn/orchestrator_app.py` (serve it at `GET /dashboard`)
- Test: `tests/cgn/test_dashboard_page.py`

**Interfaces:**
- Consumes: `/operators/{id}/nodes`, `/operators/{id}/feed`, `/operators`, `/operators/{id}/tokens`.
- Produces: one static HTML page (no external assets) with: sign-up form (posts consent), "+ Add node" button (fetches a token, shows `python -m cgn.node.worker --orchestrator <url> --token <t>`), node table, live feed table where every payload cell is the literal string `🔒 de-identified payload`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_dashboard_page.py
from fastapi.testclient import TestClient

from cgn.orchestrator_app import create_app


def test_dashboard_served():
    client = TestClient(create_app(":memory:"))
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "cgn operator dashboard" in r.text.lower()
    assert "de-identified payload" in r.text     # TR-30: the boundary, visible
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_dashboard_page.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Create the page and route**

Create `cgn/static/dashboard.html`:

```html
<!doctype html>
<meta charset="utf-8">
<title>CGN Operator Dashboard</title>
<style>
  body{font-family:system-ui;margin:2rem;max-width:960px}
  table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
  td,th{border:1px solid #ccc;padding:.4rem .6rem;text-align:left;font-size:.9rem}
  .rep{background:#eee;display:inline-block;width:100px;height:8px;border-radius:4px}
  .rep i{background:#2a7;display:block;height:8px;border-radius:4px}
  code{background:#f4f4f4;padding:.15rem .35rem;border-radius:4px}
  input,button{padding:.35rem .6rem;margin-right:.4rem}
  .lock{color:#888}
</style>
<h1>CGN operator dashboard</h1>

<section id="signup">
  <h2>Operator account</h2>
  <input id="op" placeholder="operator id"><input id="email" placeholder="email">
  <label><input type="checkbox" id="consent"> I accept the node-operator terms</label>
  <button onclick="signup()">Sign up / load</button>
</section>

<section hidden id="main">
  <h2>Your nodes <button onclick="addNode()">+ Add node</button></h2>
  <div id="enroll" hidden>Run this on the node (token is single-use, expires in 15 min):
    <p><code id="cmd"></code></p></div>
  <table><thead><tr><th>Node</th><th>Status</th><th>Reputation</th>
    <th>Jobs</th><th>Load</th></tr></thead><tbody id="nodes"></tbody></table>

  <h2>Live job feed</h2>
  <table><thead><tr><th>Job</th><th>State</th><th>Stage</th><th>Elapsed</th>
    <th>Payload</th></tr></thead><tbody id="feed"></tbody></table>
</section>

<script>
let OP = null;
async function signup(){
  const r = await fetch('/operators', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({operator_id: op.value, email: email.value,
                          consent: consent.checked})});
  if(!r.ok){ alert('consent is required'); return; }
  OP = op.value; main.hidden = false; tick();
}
async function addNode(){
  const r = await fetch(`/operators/${OP}/tokens`, {method:'POST'});
  const t = (await r.json()).token;
  cmd.textContent = `python -m cgn.node.worker --orchestrator ${location.origin} --token ${t}`;
  enroll.hidden = false;
}
async function tick(){
  if(!OP) return;
  const nodesR = await (await fetch(`/operators/${OP}/nodes`)).json();
  nodes.innerHTML = nodesR.nodes.map(n =>
    `<tr><td>${n.node_id}</td><td>${n.status}</td>
     <td><span class="rep"><i style="width:${n.reputation*100}px"></i></span></td>
     <td>${n.jobs_done}</td><td>${n.current_load}</td></tr>`).join('');
  const feedR = await (await fetch(`/operators/${OP}/feed`)).json();
  feed.innerHTML = feedR.events.map(e =>
    `<tr><td>${e.job_id}</td><td>${e.state}</td><td>${e.stage}</td>
     <td>${e.elapsed_ms} ms</td>
     <td class="lock">🔒 de-identified payload</td></tr>`).join('');
  setTimeout(tick, 2000);
}
</script>
```

In `cgn/orchestrator_app.py`, add imports `from pathlib import Path` and `from fastapi.responses import HTMLResponse`, and inside `create_app`:

```python
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        return (Path(__file__).parent / "static" / "dashboard.html").read_text()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_dashboard_page.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cgn/static/dashboard.html cgn/orchestrator_app.py tests/cgn/test_dashboard_page.py
git commit -m "feat(cgn): operator dashboard — nodes, tokens, locked live feed (TR-25/30)"
```

---

### Task 4: Preview gains add_redaction (completes M3)

**Files:**
- Modify: `cmndr/preview.py`
- Test: `tests/cmndr/test_preview_add.py`

**Interfaces:**
- Consumes: `RedactionEntry`, existing `Preview`.
- Produces: `Preview.add_redaction(original_value: str, entity_type: str) -> str | None` — placeholders every occurrence of `original_value` in `payload.text`, appends a `RedactionEntry(source="user-added")` with the next index for that type, updates `entity_summary`, returns the placeholder (None if the value does not occur).

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_preview_add.py
from cmndr.preview import Preview
from cmndr.types import PlaceholderedPayload, RedactionEntry, RedactionMap


def make_preview():
    m = RedactionMap()
    m.entries.append(RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON",
                                    (0, 10), "detector"))
    p = PlaceholderedPayload("r1", "⟦PERSON_1⟧ met Bob Jones at Initech",
                             {"PERSON": 1})
    return Preview(p, m)


def test_add_redaction_for_missed_entity():
    pv = make_preview()
    ph = pv.add_redaction("Bob Jones", "PERSON")
    assert ph == "⟦PERSON_2⟧"                      # next index for PERSON
    assert "Bob Jones" not in pv.payload.text
    assert pv.payload.entity_summary["PERSON"] == 2
    entry = pv.redaction_map.entries[-1]
    assert entry.source == "user-added"
    assert entry.original_value == "Bob Jones"


def test_add_redaction_new_type():
    pv = make_preview()
    assert pv.add_redaction("Initech", "ORG") == "⟦ORG_1⟧"
    assert pv.payload.entity_summary["ORG"] == 1


def test_add_redaction_absent_value_is_noop():
    pv = make_preview()
    assert pv.add_redaction("Nobody", "PERSON") is None
    assert pv.payload.entity_summary == {"PERSON": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_preview_add.py -v`
Expected: FAIL with `AttributeError: 'Preview' object has no attribute 'add_redaction'`

- [ ] **Step 3: Add the method**

Append to the `Preview` class in `cmndr/preview.py`:

```python
    def add_redaction(self, original_value: str, entity_type: str) -> str | None:
        from cmndr.types import RedactionEntry
        idx = self.payload.text.find(original_value)
        if idx == -1:
            return None
        n = sum(1 for e in self.redaction_map.entries
                if e.entity_type == entity_type) + 1
        placeholder = f"⟦{entity_type}_{n}⟧"
        self.payload.text = self.payload.text.replace(original_value, placeholder)
        self.redaction_map.entries.append(RedactionEntry(
            placeholder=placeholder, original_value=original_value,
            entity_type=entity_type, span=(idx, idx + len(original_value)),
            source="user-added"))
        self.payload.entity_summary[entity_type] = \
            self.payload.entity_summary.get(entity_type, 0) + 1
        return placeholder
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cmndr/test_preview_add.py tests/cmndr -q`
Expected: PASS (new tests + whole cmndr suite)

- [ ] **Step 5: Commit**

```bash
git add cmndr/preview.py tests/cmndr/test_preview_add.py
git commit -m "feat(cmndr): preview add_redaction for missed entities (M3 complete)"
```

---

### Task 5: Console backend — sessions, preview gate, lifecycle events (TR-26/27/31/32)

**Files:**
- Create: `console/__init__.py` (empty)
- Create: `console/app.py`
- Test: `tests/console/__init__.py` (empty), `tests/console/test_console_app.py`

**Interfaces:**
- Consumes: `Pipeline`, `Preview`, `cgn.contract.StatusEvent`.
- Produces: `create_console_app(pipeline: Pipeline) -> FastAPI`:
  - `POST /requests` `{payload, task_type, sensitivity_hint?}` → `{request_id}`; runs `pipeline.process_item` in a daemon thread; the approve callback parks the thread on a `threading.Event` and stores the preview.
  - `GET /requests/{id}` → `{request_id, stage, preview?, result?}`. `preview` (only while stage==`preview`): `{text, entities: [{placeholder, entity_type}]}` — placeholders and types, no original values in this JSON (they render on-device from the map only if the UI asks; MVP shows placeholders). `result` (only when done): `{route, text, restoration_anomalies}` — TR-32: this endpoint lives on the device app only.
  - `POST /requests/{id}/approve` `{accept: bool, remove: [placeholder], add: [{value, entity_type}]}` → applies edits then `accept()` (or abandons → stage `failed`), releases the thread.
  - `GET /events` → `{events: [StatusEvent-dict]}` for all requests, in emission order; states per TR-27, `node_id` None, `stage` mirrors state, `pct` staged 0.1/0.25/0.4/0.6/0.75/0.9/1.0.
  Internal: `Session` dataclass (id, stage, preview, result, error, events list, `threading.Event`).

- [ ] **Step 1: Write the failing test**

```python
# tests/console/test_console_app.py
import time

from fastapi.testclient import TestClient

from cgn.contract import StatusEvent
from cmndr.anonymizer import Anonymizer
from cmndr.backends.fake import EchoBackend
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.fake import EchoProvider
from cmndr.router import ThresholdRouter
from console.app import create_console_app


def make_client():
    pipe = Pipeline(ThresholdRouter(),
                    Anonymizer(WordlistDetector({"Jane Smith": "PERSON"})),
                    EchoBackend(), EchoProvider())
    return TestClient(create_console_app(pipe))


def wait_stage(client, rid, stage, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/requests/{rid}").json()
        if r["stage"] == stage:
            return r
        time.sleep(0.02)
    raise AssertionError(f"never reached {stage}: {r}")


def test_local_request_completes_without_preview():
    client = make_client()
    rid = client.post("/requests", json={"payload": "what is 2+2",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "low"}).json()["request_id"]
    r = wait_stage(client, rid, "done")
    assert r["result"]["route"] == "local"


def test_escalation_waits_at_preview_and_result_restores():
    client = make_client()
    rid = client.post("/requests", json={"payload": "Jane Smith owes rent",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "high"}).json()["request_id"]
    r = wait_stage(client, rid, "preview")
    assert r["preview"]["text"] == "⟦PERSON_1⟧ owes rent"      # TR-31: parked
    assert r["result"] is None

    client.post(f"/requests/{rid}/approve", json={"accept": True,
                                                  "remove": [], "add": []})
    r = wait_stage(client, rid, "done")
    assert "Jane Smith" in r["result"]["text"]                 # restored on-device


def test_rejection_fails_the_request():
    client = make_client()
    rid = client.post("/requests", json={"payload": "Jane Smith owes rent",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "high"}).json()["request_id"]
    wait_stage(client, rid, "preview")
    client.post(f"/requests/{rid}/approve", json={"accept": False,
                                                  "remove": [], "add": []})
    r = wait_stage(client, rid, "failed")
    assert r["result"] is None


def test_lifecycle_events_are_status_events_in_order():
    client = make_client()
    rid = client.post("/requests", json={"payload": "Jane Smith owes rent",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "high"}).json()["request_id"]
    wait_stage(client, rid, "preview")
    client.post(f"/requests/{rid}/approve", json={"accept": True,
                                                  "remove": [], "add": []})
    wait_stage(client, rid, "done")
    events = client.get("/events").json()["events"]
    mine = [e for e in events if e["job_id"] == rid]
    for ev in mine:
        StatusEvent.model_validate(ev)                          # TR-28 on device side
        assert "Jane Smith" not in str(ev)
    states = [e["state"] for e in mine]
    assert states == ["routing", "anonymizing", "preview",
                      "dispatched", "running", "restoring", "done"]   # TR-27
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/console/test_console_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'console'`

- [ ] **Step 3: Write the console app**

```python
# console/app.py
"""TR-26/27/31/32: the trusted-side console. A UI over the Entry Point —
console jobs run the same Pipeline as programmatic ones; the preview gate is
the same object; restored output exists only here, on the device."""

import threading
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from cgn.contract import StatusEvent
from cmndr.pipeline import Pipeline
from cmndr.preview import EscalationNotAccepted, Preview
from cmndr.types import RequestItem

_PCT = {"routing": 0.1, "anonymizing": 0.25, "preview": 0.4, "dispatched": 0.6,
        "running": 0.75, "restoring": 0.9, "done": 1.0, "failed": 1.0}


@dataclass
class Session:
    request_id: str
    stage: str = "routing"
    preview: Preview | None = None
    result: dict | None = None
    events: list[dict] = field(default_factory=list)
    gate: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False


class SubmitBody(BaseModel):
    payload: str
    task_type: str
    sensitivity_hint: str | None = None


class ApproveBody(BaseModel):
    accept: bool
    remove: list[str] = []
    add: list[dict] = []


def create_console_app(pipeline: Pipeline) -> FastAPI:
    app = FastAPI(title="cmndr-console")
    sessions: dict[str, Session] = {}
    all_events: list[dict] = []

    def emit(s: Session, state: str) -> None:
        s.stage = state
        ev = StatusEvent(job_id=s.request_id, state=state, pct=_PCT[state],
                         node_id=None, elapsed_ms=0, stage=state).model_dump()
        s.events.append(ev)
        all_events.append(ev)

    def run(s: Session, item: RequestItem) -> None:
        def approve(preview: Preview) -> None:
            emit(s, "anonymizing")
            s.preview = preview
            emit(s, "preview")
            s.gate.wait()
            if s.accepted:
                preview.accept()
                emit(s, "dispatched")
                emit(s, "running")

        try:
            resp = pipeline.process_item(item, approve=approve)
            if resp.route == "escalate":
                emit(s, "restoring")
            else:
                emit(s, "running")
            s.result = {"route": resp.route, "text": resp.text,
                        "restoration_anomalies": resp.restoration_anomalies}
            emit(s, "done")
        except EscalationNotAccepted:
            emit(s, "failed")
        except Exception:
            emit(s, "failed")

    @app.post("/requests")
    def submit(body: SubmitBody):
        rid = f"req-{uuid.uuid4().hex[:12]}"
        s = Session(request_id=rid)
        sessions[rid] = s
        emit(s, "routing")
        item = RequestItem(rid, body.task_type, body.payload,
                           sensitivity_hint=body.sensitivity_hint)
        threading.Thread(target=run, args=(s, item), daemon=True).start()
        return {"request_id": rid}

    @app.get("/requests/{rid}")
    def status(rid: str):
        s = sessions.get(rid)
        if s is None:
            raise HTTPException(status_code=404)
        preview = None
        if s.stage == "preview" and s.preview is not None:
            preview = {"text": s.preview.payload.text,
                       "entities": [{"placeholder": e.placeholder,
                                     "entity_type": e.entity_type}
                                    for e in s.preview.redaction_map.entries]}
        return {"request_id": rid, "stage": s.stage,
                "preview": preview, "result": s.result}

    @app.post("/requests/{rid}/approve")
    def approve(rid: str, body: ApproveBody):
        s = sessions.get(rid)
        if s is None or s.preview is None:
            raise HTTPException(status_code=404)
        for add in body.add:
            s.preview.add_redaction(add["value"], add["entity_type"])
        for ph in body.remove:
            s.preview.remove_redaction(ph)
        s.accepted = body.accept
        s.gate.set()
        return {"ok": True}

    @app.get("/events")
    def events():
        return {"events": list(all_events)}

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/console/test_console_app.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add console tests/console
git commit -m "feat(console): trusted-side console API — preview gate + TR-27 lifecycle"
```

---

### Task 6: Console UI — the decision-tree page (TR-29)

**Files:**
- Create: `console/static/console.html`
- Modify: `console/app.py` (serve at `GET /`, plus `main()` runner)
- Test: `tests/console/test_console_page.py`

**Interfaces:**
- Consumes: the Task-5 endpoints.
- Produces: single HTML page: prompt form → stage pipeline rendered as the top-down flow (routing → local | escalate branch; the taken path highlighted), preview panel (payload text, entity chips, remove buttons, add-redaction inputs, Approve/Reject), result panel (trusted-side only). `python -m console.app --orchestrator URL --model MODEL` runs the device app on port 8100 with `HeuristicRouter`+`PresidioDetector` when available, else stub router+detector, `LlamaCppBackend` when available else `EchoBackend`, `CGNProvider` against the given orchestrator.

- [ ] **Step 1: Write the failing test**

```python
# tests/console/test_console_page.py
from fastapi.testclient import TestClient

from cmndr.anonymizer import Anonymizer
from cmndr.backends.fake import EchoBackend
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.fake import EchoProvider
from cmndr.router import ThresholdRouter
from console.app import create_console_app


def test_console_page_served():
    pipe = Pipeline(ThresholdRouter(), Anonymizer(WordlistDetector({})),
                    EchoBackend(), EchoProvider())
    client = TestClient(create_console_app(pipe))
    r = client.get("/")
    assert r.status_code == 200
    assert "cmndr console" in r.text.lower()
    for stage in ("routing", "anonymizing", "preview", "dispatched",
                  "running", "restoring", "done"):
        assert stage in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/console/test_console_page.py -v`
Expected: FAIL — 404 on `/`

- [ ] **Step 3: Create the page and wire it**

Create `console/static/console.html`:

```html
<!doctype html>
<meta charset="utf-8">
<title>cmndr console</title>
<style>
  body{font-family:system-ui;margin:2rem;max-width:820px}
  textarea{width:100%;height:90px}
  .flow{display:flex;gap:.4rem;flex-wrap:wrap;margin:1rem 0}
  .stage{padding:.35rem .7rem;border:1px solid #bbb;border-radius:999px;
         color:#999;font-size:.85rem}
  .stage.on{background:#2a7;border-color:#2a7;color:#fff}
  .stage.fail{background:#c33;border-color:#c33;color:#fff}
  #previewBox,#resultBox{border:1px solid #ccc;border-radius:8px;
    padding:1rem;margin:1rem 0;display:none}
  .chip{display:inline-block;background:#eef;border:1px solid #99c;
    border-radius:4px;padding:.1rem .4rem;margin:.15rem;font-size:.8rem}
  pre{white-space:pre-wrap;background:#f7f7f7;padding:.7rem;border-radius:6px}
  button{padding:.4rem .8rem}
</style>
<h1>cmndr console</h1>
<p>Prompt enters here and never leaves the device un-anonymized.</p>
<textarea id="payload" placeholder="Paste a document or ask a question…"></textarea>
<p>
  <select id="task"><option>summarize</option><option>extract</option>
    <option>classify</option></select>
  <button onclick="submitReq()">Run</button>
</p>

<div class="flow" id="flow">
  <span class="stage" data-s="routing">routing</span>
  <span class="stage" data-s="anonymizing">anonymizing</span>
  <span class="stage" data-s="preview">preview</span>
  <span class="stage" data-s="dispatched">dispatched</span>
  <span class="stage" data-s="running">running</span>
  <span class="stage" data-s="restoring">restoring</span>
  <span class="stage" data-s="done">done</span>
</div>

<div id="previewBox">
  <h3>Pre-flight preview — exactly what will leave this device</h3>
  <pre id="previewText"></pre>
  <div id="chips"></div>
  <p><input id="addVal" placeholder="missed value"> as
     <input id="addType" placeholder="TYPE" size="8">
     <button onclick="addRedaction()">Redact it</button></p>
  <p><button onclick="approve(true)">Approve &amp; send</button>
     <button onclick="approve(false)">Reject</button></p>
</div>

<div id="resultBox"><h3>Result (rendered trusted-side only)</h3>
  <pre id="resultText"></pre></div>

<script>
let RID = null, edits = {remove: [], add: []};
async function submitReq(){
  edits = {remove: [], add: []};
  resultBox.style.display = 'none';
  const r = await fetch('/requests', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({payload: payload.value, task_type: task.value,
                          sensitivity_hint: null})});
  RID = (await r.json()).request_id;
  poll();
}
function light(stage, failed){
  document.querySelectorAll('.stage').forEach(el => {
    el.classList.toggle('on', !failed && el.dataset.s === stage);
    el.classList.toggle('fail', failed && el.dataset.s === stage);
  });
}
async function poll(){
  const r = await (await fetch(`/requests/${RID}`)).json();
  light(r.stage === 'failed' ? 'preview' : r.stage, r.stage === 'failed');
  if(r.stage === 'preview'){
    previewText.textContent = r.preview.text;
    chips.innerHTML = r.preview.entities.map(e =>
      `<span class="chip">${e.placeholder} ${e.entity_type}
        <a href="#" onclick="removePh('${e.placeholder}');return false">✕</a></span>`).join('');
    previewBox.style.display = 'block';
  } else { previewBox.style.display = 'none'; }
  if(r.stage === 'done'){
    resultText.textContent = r.result.text;
    resultBox.style.display = 'block';
    return;
  }
  if(r.stage !== 'failed') setTimeout(poll, 250);
}
function removePh(ph){ edits.remove.push(ph); approvePending(); }
function addRedaction(){
  edits.add.push({value: addVal.value, entity_type: addType.value || 'CUSTOM'});
  approvePending();
}
let pendingSent = false;
async function approvePending(){ /* edits applied at approve time */ }
async function approve(accept){
  await fetch(`/requests/${RID}/approve`, {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({accept, remove: edits.remove, add: edits.add})});
  poll();
}
</script>
```

In `console/app.py`, add to imports: `from pathlib import Path` and `from fastapi.responses import HTMLResponse`. Inside `create_console_app`, add:

```python
    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "console.html").read_text()
```

At module bottom, add the runner:

```python
def main() -> None:
    import argparse

    import httpx
    import uvicorn

    from cmndr.anonymizer import Anonymizer
    from cmndr.audit import AuditLog
    from cmndr.backends.fake import EchoBackend
    from cmndr.detect import WordlistDetector
    from cmndr.providers.cgn import CGNProvider
    from cmndr.router import ThresholdRouter

    p = argparse.ArgumentParser()
    p.add_argument("--orchestrator", default="http://localhost:8000")
    p.add_argument("--model", default="mistral-7b-instruct-v0.2.Q4_K_M")
    p.add_argument("--port", type=int, default=8100)
    args = p.parse_args()

    from cmndr.detectors.presidio import PresidioDetector, spacy_model_available
    if spacy_model_available("en_core_web_sm"):
        detector = PresidioDetector()
        from cmndr.routers.heuristic import HeuristicRouter
        router = HeuristicRouter(detector)
    else:
        detector, router = WordlistDetector({}), ThresholdRouter()

    from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                         llamacpp_available)
    backend = (LlamaCppBackend(DEFAULT_MODEL_PATH)
               if llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()
               else EchoBackend())

    pipeline = Pipeline(router, Anonymizer(detector), backend,
                        CGNProvider(httpx.Client(base_url=args.orchestrator,
                                                 timeout=300), args.model),
                        audit=AuditLog("console_audit.jsonl"))
    uvicorn.run(create_console_app(pipeline), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/console -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add console tests/console
git commit -m "feat(console): decision-flow UI with editable preview gate (TR-29)"
```

---

### Task 7: Legacy cleanup + full-system verification

**Files:**
- Delete: `orchestrator/`, `node_agent/`, `anonymizer/`, `client/`, `frontend/`, `tests/test_orchestrator.py`, `tests/test_node_agent.py`, `tests/test_anonymizer.py`, `tests/stress_test.py`, `plan.md`, `new_plan.md`, `orchestrator.db`, `Dockerfile.node`
- Modify: `pyproject.toml` (packages), `Dockerfile`, `railway.toml` if it references deleted paths

**Interfaces:**
- Consumes: nothing new. The repo's single device pipeline is `cmndr/` + `console/`; the single network is `cgn/`.

- [ ] **Step 1: Verify nothing live imports the legacy packages**

Run: `grep -rn "from anonymizer\|import anonymizer\|from orchestrator\|from node_agent\|from client\b" cmndr cgn console eval tests/cmndr tests/cgn tests/console`
Expected: no output. If there are hits, fix them first — do not delete while referenced.

- [ ] **Step 2: Delete the legacy code**

```bash
git rm -r orchestrator node_agent anonymizer client frontend \
  tests/test_orchestrator.py tests/test_node_agent.py tests/test_anonymizer.py \
  tests/stress_test.py Dockerfile.node
git rm --cached orchestrator.db 2>/dev/null; rm -f orchestrator.db
git rm plan.md 2>/dev/null; rm -f new_plan.md
```

- [ ] **Step 3: Update packaging**

In `pyproject.toml` set:

```toml
[tool.hatch.build.targets.wheel]
packages = ["cmndr", "cgn", "console", "eval"]
```

In `Dockerfile`, change the serve command to the CGN orchestrator:

```dockerfile
CMD ["uvicorn", "--factory", "cgn.orchestrator_app:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

(If the existing Dockerfile copies `orchestrator/`, change the COPY to `cgn/`.) `create_app` takes `db_path` with a default, so the factory works; production sets a volume-backed path via an env-reading wrapper only if Railway deploy is actually exercised — otherwise leave the default and note it in the commit body.

- [ ] **Step 4: Run the entire suite**

Run: `uv run pytest tests -q`
Expected: PASS — only `tests/cmndr`, `tests/cgn`, `tests/console` remain, all green.

- [ ] **Step 5: Smoke the demo**

Run: `uv run python -m cmndr.demo`
Expected: the Plan-1 LOCAL/ESCAL lines print unchanged.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: retire legacy orchestrator/node_agent/anonymizer/client — cgn+cmndr+console are the system"
```

---

## Self-Review

**1. Spec coverage:**
- TR-21 (account + consent before enroll) → Task 1 (+ Plan-3 Task 4 gate). ✅
- TR-25 (operator sees status/health live) → Tasks 1, 3. ✅
- TR-28 (metadata-only stream, enforced in code) → Plan-3 Task 1 schema; producers in Task 2 (Zone 2) and Task 5 (device) both construct through `StatusEvent`; tests assert `model_validate` + content absence. ✅
- TR-30 (own-node metadata feed) → Tasks 2, 3 (op2 isolation test; locked payload cell). ✅
- TR-26 (console jobs flow through the same pipeline; raw input reaches Anonymizer only) → Task 5 (console wraps the shared `Pipeline`). ✅
- TR-27 (lifecycle sequence with events per transition) → Task 5 events test asserts the exact order. ✅
- TR-29 (live monitor + review action + result) → Task 6 page (poll loop, preview panel, result panel). ✅
- TR-31 (no console bypass of preview) → Task 5: escalation parks on the gate; reject → failed, never dispatched. ✅
- TR-32 (restored output only trusted-side) → result lives only in console app memory/endpoint; feed/dashboard show `🔒 de-identified payload`. ✅
- M3 add-missed-redaction → Task 4. RD1 (token + command) → Task 3 drawer. §8.3 (frontend never touches keys/jobs) → dashboard only calls account/token/metadata endpoints. ✅
- Design §2 salvage table end-state (legacy retired) → Task 7. ✅

**2. Placeholder scan:** all steps carry complete code/commands; the only conditional is Dockerfile COPY wording, which states exactly what to change. ✅

**3. Type consistency:** `StatusEvent` field set identical to Plan-3 Task 1; console states match the TR-27 list used in tests; `add_redaction(value, entity_type)` (Task 4) matches its console call `add_redaction(add["value"], add["entity_type"])` (Task 5); `create_console_app(pipeline)` consistent across Tasks 5/6; operator endpoints in Task 3's JS match Task 1/2 routes. ✅
