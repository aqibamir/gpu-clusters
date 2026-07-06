# cmndr CGN Network Implementation Plan (Plan 3 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the GPU network to the ratified trust model — enrollment tokens + node-side keypairs (TR-22/23), pull dispatch with capability matching and reassignment (TR-13/14), signed results verified on-device before restoration (TR-17), canary attestation (TR-24), redundant-dispatch verification + reputation ejection (TR-18/19), and a PII-blind orchestrator (TR-15/16) — plugged into the device pipeline behind the existing `Provider` seam.

**Architecture:** New `cgn/` package: `contract.py` (frozen §12.0 wire schemas), `db.py` (SQLite), `registry.py` (tokens/enroll/heartbeat), `dispatch.py` (queue/assign/results/timeouts), `reputation.py` (scores/divergence/ejection), `orchestrator_app.py` (FastAPI app factory), `node/identity.py` (Ed25519), `node/worker.py` (enroll → heartbeat → poll → infer → sign → post). Device side gains `cmndr/providers/cgn.py` (`CGNProvider`), which verifies node signatures before the pipeline restores. Nodes only ever dial out (CD1); device↔node always via orchestrator (CD2). The old `orchestrator/`, `node_agent/`, `client/` packages are untouched here and deleted in Plan 4's cleanup.

**Tech Stack:** Python 3.11+, FastAPI + pydantic v2, SQLite (stdlib `sqlite3`), `cryptography` Ed25519 (already a dep), httpx / FastAPI `TestClient` (a real `httpx.Client`) so the provider and worker run identically against in-process apps and real URLs.

## Global Constraints

- Python ≥ 3.11; new code under `cgn/` (plus one file under `cmndr/providers/`); tests under `tests/cgn/`.
- The §12.0 wire contract in `cgn/contract.py` is frozen by Task 1; later tasks may not add or rename its fields.
- `StatusEvent` carries exactly `job_id, state, pct, node_id, elapsed_ms, stage` — schema rejects extras in code (TR-28), enforced with pydantic `extra="forbid"`.
- No CGN component ever receives raw PII or a `RedactionMap` (TR-15/16); jobs carry `placeholdered_prompt` only, no customer id.
- Node private keys are generated on the node and never transmitted (TR-23).
- Reputation constants: start `1.0`, pass `+0.05` (cap 1.0), divergence/timeout `-0.2`, ejection floor `0.3`.
- Node worker and provider take an injected `httpx.Client`; tests inject `fastapi.testclient.TestClient(app)`.
- Every code-bearing task is TDD: failing test → run-fail → minimal impl → run-pass → commit. Run tests with `uv run pytest …`.

---

### Task 1: Frozen wire contract (§12.0) + TR-28 schema enforcement

**Files:**
- Create: `cgn/__init__.py` (empty), `cgn/node/__init__.py` (empty), `tests/cgn/__init__.py` (empty)
- Create: `cgn/contract.py`
- Modify: `pyproject.toml` (hatch wheel packages)
- Test: `tests/cgn/test_contract.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all pydantic `BaseModel`): `Capabilities{models: list[str], max_context: int, throughput_hint: str}`, `EnrollRequest{enrollment_token, node_public_key, capabilities: Capabilities, endpoint_info}`, `EnrollResponse{node_id, status}`, `HeartbeatRequest{node_id, signature, current_load: int}`, `HeartbeatResponse{ack: bool, dispatch_eligible: bool}`, `Job{job_id, model_spec, placeholdered_prompt, params: dict, issued_at: float, timeout: float, job_signature}`, `Result{job_id, placeholdered_completion, node_id, node_signature, completed_at: float}`, `StatusEvent{job_id, state, pct: float, node_id: str | None, elapsed_ms: int, stage}` with `model_config = ConfigDict(extra="forbid")` on **every** model.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_contract.py
import pytest
from pydantic import ValidationError

from cgn.contract import (Capabilities, EnrollRequest, HeartbeatRequest, Job,
                          Result, StatusEvent)


def test_status_event_accepts_exact_schema():
    ev = StatusEvent(job_id="j1", state="running", pct=0.4,
                     node_id="n1", elapsed_ms=1200, stage="inference")
    assert ev.state == "running"


def test_status_event_rejects_content_fields():
    # TR-28: the metadata-only rule is enforced in code, not convention
    with pytest.raises(ValidationError):
        StatusEvent(job_id="j1", state="running", pct=0.4, node_id="n1",
                    elapsed_ms=1, stage="inference",
                    placeholdered_prompt="⟦PERSON_1⟧ owes rent")


def test_job_carries_no_customer_identity_field():
    assert "customer_id" not in Job.model_fields
    assert "raw_payload" not in Job.model_fields


def test_enroll_request_shape():
    req = EnrollRequest(enrollment_token="t", node_public_key="ab12",
                        capabilities=Capabilities(models=["m"], max_context=4096,
                                                  throughput_hint="laptop"),
                        endpoint_info="outbound-only")
    assert req.capabilities.models == ["m"]


def test_result_and_heartbeat_reject_extras():
    with pytest.raises(ValidationError):
        Result(job_id="j", placeholdered_completion="c", node_id="n",
               node_signature="s", completed_at=1.0, raw="nope")
    with pytest.raises(ValidationError):
        HeartbeatRequest(node_id="n", signature="s", current_load=0, extra=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/contract.py
"""§12.0 — the frozen shared wire contract. Changing this file is the only
thing that requires both tracks to sync. Every model forbids unknown fields;
for StatusEvent that IS the TR-28 metadata-only guarantee."""

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capabilities(_Frozen):
    models: list[str]
    max_context: int
    throughput_hint: str


class EnrollRequest(_Frozen):
    enrollment_token: str
    node_public_key: str  # hex; ONLY the public key ever leaves the node (TR-23)
    capabilities: Capabilities
    endpoint_info: str


class EnrollResponse(_Frozen):
    node_id: str
    status: str  # "pending-verification"


class HeartbeatRequest(_Frozen):
    node_id: str
    signature: str  # over "hb|{node_id}|{current_load}"
    current_load: int


class HeartbeatResponse(_Frozen):
    ack: bool
    dispatch_eligible: bool


class Job(_Frozen):
    job_id: str
    model_spec: str
    placeholdered_prompt: str  # the ONLY content field; already placeholdered
    params: dict
    issued_at: float
    timeout: float
    job_signature: str  # sha256 checksum of (job_id|model_spec|prompt)


class Result(_Frozen):
    job_id: str
    placeholdered_completion: str
    node_id: str
    node_signature: str  # Ed25519 over "{job_id}|{completion}", hex
    completed_at: float


class StatusEvent(_Frozen):
    job_id: str
    state: str      # queued|assigned|running|done|failed|reassigned|divergent
    pct: float
    node_id: str | None
    elapsed_ms: int
    stage: str
```

- [ ] **Step 4: Register packages in pyproject**

In `pyproject.toml`, change the hatch wheel packages line to:

```toml
[tool.hatch.build.targets.wheel]
packages = ["node_agent", "orchestrator", "anonymizer", "client", "cmndr", "cgn", "eval"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_contract.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add cgn tests/cgn pyproject.toml
git commit -m "feat(cgn): frozen §12.0 wire contract; StatusEvent enforces metadata-only (TR-28)"
```

---

### Task 2: CGN SQLite schema

**Files:**
- Create: `cgn/db.py`
- Test: `tests/cgn/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `connect(path: str) -> sqlite3.Connection` (row_factory=Row, schema initialized, `check_same_thread=False`). Tables: `operators(operator_id PK, email, consent_at REAL)`, `enrollment_tokens(token PK, operator_id, created_at REAL, expires_at REAL, spent INTEGER DEFAULT 0)`, `nodes(node_id PK, operator_id, public_key, capabilities TEXT/*json*/, endpoint_info, status TEXT DEFAULT 'pending-verification', reputation REAL DEFAULT 1.0, last_heartbeat REAL DEFAULT 0, current_load INTEGER DEFAULT 0)`, `jobs(job_id PK, kind TEXT DEFAULT 'inference' /*inference|canary*/, model_spec, prompt, params TEXT, state TEXT DEFAULT 'queued', redundant INTEGER DEFAULT 0, canary_nonce TEXT, created_at REAL, timeout_s REAL DEFAULT 120, attempts INTEGER DEFAULT 0)`, `assignments(job_id, node_id, assigned_at REAL, state TEXT DEFAULT 'assigned' /*assigned|done|timeout*/, completion TEXT, signature TEXT, completed_at REAL, PRIMARY KEY (job_id, node_id))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_db.py
from cgn.db import connect


def test_schema_tables_exist():
    conn = connect(":memory:")
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"operators", "enrollment_tokens", "nodes", "jobs", "assignments"} <= names


def test_defaults():
    conn = connect(":memory:")
    conn.execute("INSERT INTO nodes (node_id, operator_id, public_key, capabilities, endpoint_info) "
                 "VALUES ('n1','op1','ab','{}','out')")
    row = conn.execute("SELECT * FROM nodes").fetchone()
    assert row["status"] == "pending-verification"
    assert row["reputation"] == 1.0
    conn.execute("INSERT INTO jobs (job_id, model_spec, prompt, params, created_at) "
                 "VALUES ('j1','m','p','{}',0)")
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["state"] == "queued"
    assert job["kind"] == "inference"
    assert job["attempts"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.db'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/db.py
"""CGN persistence. Replaces the legacy credit-ledger schema (CD5: no payment).
PII-blind by construction: jobs hold placeholdered prompts only (TR-16)."""

import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS operators (
    operator_id TEXT PRIMARY KEY,
    email       TEXT,
    consent_at  REAL
);
CREATE TABLE IF NOT EXISTS enrollment_tokens (
    token       TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    spent       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,
    operator_id    TEXT NOT NULL,
    public_key     TEXT NOT NULL,
    capabilities   TEXT NOT NULL,
    endpoint_info  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending-verification',
    reputation     REAL NOT NULL DEFAULT 1.0,
    last_heartbeat REAL NOT NULL DEFAULT 0,
    current_load   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'inference',
    model_spec   TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',
    state        TEXT NOT NULL DEFAULT 'queued',
    redundant    INTEGER NOT NULL DEFAULT 0,
    canary_nonce TEXT,
    created_at   REAL NOT NULL,
    timeout_s    REAL NOT NULL DEFAULT 120,
    attempts     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS assignments (
    job_id       TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    assigned_at  REAL NOT NULL,
    state        TEXT NOT NULL DEFAULT 'assigned',
    completion   TEXT,
    signature    TEXT,
    completed_at REAL,
    PRIMARY KEY (job_id, node_id)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/db.py tests/cgn/test_db.py
git commit -m "feat(cgn): SQLite schema — nodes, tokens, jobs, assignments; no credit ledger (CD5)"
```

---

### Task 3: Node identity — local Ed25519 keypair + signing (TR-23 / TR-17 primitives)

**Files:**
- Create: `cgn/node/identity.py`
- Test: `tests/cgn/test_identity.py`

**Interfaces:**
- Consumes: `cryptography.hazmat.primitives.asymmetric.ed25519`.
- Produces: `NodeIdentity` with classmethods `create(key_dir: Path) -> NodeIdentity` (generates, writes `node_key.pem` chmod 600) and `load(key_dir) -> NodeIdentity`; properties/methods `public_key_hex: str`, `sign(message: str) -> str` (hex). Module function `verify(public_key_hex: str, message: str, signature_hex: str) -> bool` — used later by both registry (heartbeats) and device (results).

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_identity.py
from cgn.node.identity import NodeIdentity, verify


def test_create_writes_private_key_locally_and_roundtrips(tmp_path):
    ident = NodeIdentity.create(tmp_path)
    assert (tmp_path / "node_key.pem").exists()
    sig = ident.sign("hello")
    assert verify(ident.public_key_hex, "hello", sig)


def test_load_reuses_same_identity(tmp_path):
    a = NodeIdentity.create(tmp_path)
    b = NodeIdentity.load(tmp_path)
    assert a.public_key_hex == b.public_key_hex


def test_verify_rejects_tampered_message(tmp_path):
    ident = NodeIdentity.create(tmp_path)
    sig = ident.sign("job1|answer")
    assert not verify(ident.public_key_hex, "job1|forged", sig)


def test_verify_rejects_wrong_key(tmp_path):
    a = NodeIdentity.create(tmp_path / "a")
    b = NodeIdentity.create(tmp_path / "b")
    sig = a.sign("m")
    assert not verify(b.public_key_hex, "m", sig)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_identity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.node.identity'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/node/identity.py
"""TR-23: the node generates its keypair locally; the private key never
leaves the machine. Only `public_key_hex` appears in enrollment traffic."""

from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

_KEY_FILE = "node_key.pem"


class NodeIdentity:
    def __init__(self, private_key: ed25519.Ed25519PrivateKey) -> None:
        self._private = private_key

    @classmethod
    def create(cls, key_dir: Path) -> "NodeIdentity":
        key_dir = Path(key_dir)
        key_dir.mkdir(parents=True, exist_ok=True)
        key = ed25519.Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        path = key_dir / _KEY_FILE
        path.write_bytes(pem)
        path.chmod(0o600)
        return cls(key)

    @classmethod
    def load(cls, key_dir: Path) -> "NodeIdentity":
        pem = (Path(key_dir) / _KEY_FILE).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        return cls(key)

    @property
    def public_key_hex(self) -> str:
        return self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def sign(self, message: str) -> str:
        return self._private.sign(message.encode()).hex()


def verify(public_key_hex: str, message: str, signature_hex: str) -> bool:
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), message.encode())
        return True
    except (InvalidSignature, ValueError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_identity.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/node/identity.py tests/cgn/test_identity.py
git commit -m "feat(cgn): node-local Ed25519 identity; private key never transmitted (TR-23)"
```

---

### Task 4: Registry — tokens, enrollment, heartbeat liveness (TR-12 / TR-22)

**Files:**
- Create: `cgn/registry.py`
- Test: `tests/cgn/test_registry.py`

**Interfaces:**
- Consumes: `cgn.db.connect`, `cgn.contract` models, `cgn.node.identity.verify`.
- Produces: `issue_token(conn, operator_id: str, ttl_s: float = 900, now: float | None = None) -> str`; `enroll(conn, req: EnrollRequest, now: float | None = None) -> EnrollResponse` (raises `EnrollmentError(reason)` for invalid/expired/spent token or missing operator consent); `heartbeat(conn, req: HeartbeatRequest, now: float | None = None) -> HeartbeatResponse` (verifies signature against stored pubkey, updates `last_heartbeat`/`current_load`); `alive_nodes(conn, now, ttl_s: float = 90) -> list[sqlite3.Row]`; `EnrollmentError(Exception)` with `.reason: str`. Node ids are `f"node-{uuid4().hex[:12]}"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_registry.py
import pytest

from cgn.contract import Capabilities, EnrollRequest, HeartbeatRequest
from cgn.db import connect
from cgn.node.identity import NodeIdentity
from cgn.registry import (EnrollmentError, alive_nodes, enroll, heartbeat,
                          issue_token)

CAPS = Capabilities(models=["echo-model"], max_context=4096, throughput_hint="laptop")


def setup_conn():
    conn = connect(":memory:")
    conn.execute("INSERT INTO operators (operator_id, email, consent_at) "
                 "VALUES ('op1', 'op@example.com', 100.0)")
    conn.commit()
    return conn


def enroll_node(conn, ident, now=1000.0):
    token = issue_token(conn, "op1", now=now)
    return enroll(conn, EnrollRequest(
        enrollment_token=token, node_public_key=ident.public_key_hex,
        capabilities=CAPS, endpoint_info="outbound-only"), now=now)


def test_valid_token_enrolls_pending(tmp_path):
    conn = setup_conn()
    resp = enroll_node(conn, NodeIdentity.create(tmp_path))
    assert resp.status == "pending-verification"
    row = conn.execute("SELECT * FROM nodes").fetchone()
    assert row["operator_id"] == "op1"


def test_token_is_single_use(tmp_path):
    conn = setup_conn()
    ident = NodeIdentity.create(tmp_path)
    token = issue_token(conn, "op1", now=1000.0)
    req = EnrollRequest(enrollment_token=token, node_public_key=ident.public_key_hex,
                        capabilities=CAPS, endpoint_info="o")
    enroll(conn, req, now=1000.0)
    with pytest.raises(EnrollmentError) as e:
        enroll(conn, req, now=1001.0)
    assert e.value.reason == "spent"


def test_expired_token_rejected(tmp_path):
    conn = setup_conn()
    token = issue_token(conn, "op1", ttl_s=10, now=1000.0)
    ident = NodeIdentity.create(tmp_path)
    with pytest.raises(EnrollmentError) as e:
        enroll(conn, EnrollRequest(enrollment_token=token,
                                   node_public_key=ident.public_key_hex,
                                   capabilities=CAPS, endpoint_info="o"), now=1011.0)
    assert e.value.reason == "expired"


def test_enroll_without_consented_operator_rejected(tmp_path):
    conn = connect(":memory:")  # no operator row at all
    conn.execute("INSERT INTO enrollment_tokens VALUES ('t','ghost',1000,2000,0)")
    ident = NodeIdentity.create(tmp_path)
    with pytest.raises(EnrollmentError) as e:
        enroll(conn, EnrollRequest(enrollment_token="t",
                                   node_public_key=ident.public_key_hex,
                                   capabilities=CAPS, endpoint_info="o"), now=1500.0)
    assert e.value.reason == "no-consent"


def test_heartbeat_updates_liveness_and_bad_signature_rejected(tmp_path):
    conn = setup_conn()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll_node(conn, ident).node_id
    ok = heartbeat(conn, HeartbeatRequest(
        node_id=node_id, signature=ident.sign(f"hb|{node_id}|0"), current_load=0),
        now=2000.0)
    assert ok.ack is True
    bad = heartbeat(conn, HeartbeatRequest(
        node_id=node_id, signature="00" * 64, current_load=0), now=2001.0)
    assert bad.ack is False
    assert [r["node_id"] for r in alive_nodes(conn, now=2050.0)] == [node_id]
    assert alive_nodes(conn, now=5000.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/registry.py
"""§7.2/§8: node registry. Registration is open but registration ≠ trust —
trust arrives via canary (Task 6) and reputation (Task 8). Tokens are
single-use, time-limited, account-bound (TR-22); consent is checked (TR-21)."""

import json
import secrets
import time
import uuid

from cgn.contract import EnrollRequest, EnrollResponse, HeartbeatRequest, HeartbeatResponse
from cgn.node.identity import verify

HEARTBEAT_TTL_S = 90


class EnrollmentError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def issue_token(conn, operator_id: str, ttl_s: float = 900,
                now: float | None = None) -> str:
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO enrollment_tokens VALUES (?,?,?,?,0)",
                 (token, operator_id, now, now + ttl_s))
    conn.commit()
    return token


def enroll(conn, req: EnrollRequest, now: float | None = None) -> EnrollResponse:
    now = time.time() if now is None else now
    row = conn.execute("SELECT * FROM enrollment_tokens WHERE token=?",
                       (req.enrollment_token,)).fetchone()
    if row is None:
        raise EnrollmentError("invalid")
    if row["spent"]:
        raise EnrollmentError("spent")
    if now > row["expires_at"]:
        raise EnrollmentError("expired")
    op = conn.execute(
        "SELECT * FROM operators WHERE operator_id=? AND consent_at IS NOT NULL",
        (row["operator_id"],)).fetchone()
    if op is None:
        raise EnrollmentError("no-consent")

    node_id = f"node-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO nodes (node_id, operator_id, public_key, capabilities, endpoint_info) "
        "VALUES (?,?,?,?,?)",
        (node_id, row["operator_id"], req.node_public_key,
         json.dumps(req.capabilities.model_dump()), req.endpoint_info))
    conn.execute("UPDATE enrollment_tokens SET spent=1 WHERE token=?",
                 (req.enrollment_token,))
    conn.commit()
    return EnrollResponse(node_id=node_id, status="pending-verification")


def heartbeat(conn, req: HeartbeatRequest, now: float | None = None) -> HeartbeatResponse:
    now = time.time() if now is None else now
    row = conn.execute("SELECT * FROM nodes WHERE node_id=?", (req.node_id,)).fetchone()
    if row is None:
        return HeartbeatResponse(ack=False, dispatch_eligible=False)
    if not verify(row["public_key"], f"hb|{req.node_id}|{req.current_load}", req.signature):
        return HeartbeatResponse(ack=False, dispatch_eligible=False)
    conn.execute("UPDATE nodes SET last_heartbeat=?, current_load=? WHERE node_id=?",
                 (now, req.current_load, req.node_id))
    conn.commit()
    return HeartbeatResponse(ack=True, dispatch_eligible=row["status"] == "eligible")


def alive_nodes(conn, now: float | None = None, ttl_s: float = HEARTBEAT_TTL_S):
    now = time.time() if now is None else now
    return conn.execute("SELECT * FROM nodes WHERE last_heartbeat >= ?",
                        (now - ttl_s,)).fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_registry.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/registry.py tests/cgn/test_registry.py
git commit -m "feat(cgn): registry — single-use tokens, consent gate, signed heartbeats (TR-12/21/22)"
```

---

### Task 5: Dispatch — queue, capability match, pull assignment, timeout reassignment (TR-13 / TR-14)

**Files:**
- Create: `cgn/dispatch.py`
- Test: `tests/cgn/test_dispatch.py`

**Interfaces:**
- Consumes: `cgn.db`, `cgn.registry.alive_nodes`, `cgn.contract.Job/Result`.
- Produces: `submit_job(conn, model_spec, placeholdered_prompt, params=None, redundant=False, timeout_s=120.0, kind="inference", canary_nonce=None, node_hint=None, now=None) -> str` (job_id); `poll_for_work(conn, node_id, now=None) -> Job | None` (assigns the oldest queued job the node can serve; a redundant job needs 2 distinct nodes; `node_hint` pins canaries to their node); `submit_result(conn, result: Result, now=None) -> None`; `check_timeouts(conn, now=None) -> list[str]` (returns re-queued job ids; assignment marked `timeout`; job attempts+1; ≥3 attempts → state `failed`); `job_status(conn, job_id) -> dict` with keys `state`, `completions: list[dict(node_id, completion, signature)]`. Selection order: status `eligible`, alive, capability match, `reputation DESC, current_load ASC`. `MAX_ATTEMPTS = 3`. Job signature = `sha256("{job_id}|{model_spec}|{prompt}")` hexdigest.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_dispatch.py
from cgn.contract import Result
from cgn.db import connect
from cgn.dispatch import (check_timeouts, job_status, poll_for_work,
                          submit_job, submit_result)


def add_node(conn, node_id, models='["echo-model"]', status="eligible",
             reputation=1.0, hb=1000.0):
    conn.execute(
        "INSERT INTO nodes (node_id, operator_id, public_key, capabilities, "
        "endpoint_info, status, reputation, last_heartbeat) VALUES (?,?,?,?,?,?,?,?)",
        (node_id, "op1", "aa", f'{{"models": {models}, "max_context": 4096, '
         f'"throughput_hint": "x"}}', "o", status, reputation, hb))
    conn.commit()


def test_capable_node_gets_job_incapable_does_not():
    conn = connect(":memory:")
    add_node(conn, "n1")
    add_node(conn, "n2", models='["other-model"]')
    jid = submit_job(conn, "echo-model", "⟦PERSON_1⟧ owes rent", now=1000.0)
    assert poll_for_work(conn, "n2", now=1001.0) is None      # TR-13
    job = poll_for_work(conn, "n1", now=1001.0)
    assert job is not None and job.job_id == jid
    assert job.placeholdered_prompt == "⟦PERSON_1⟧ owes rent"
    # second poll: nothing left
    assert poll_for_work(conn, "n1", now=1002.0) is None


def test_pending_or_dead_nodes_never_polled_work():
    conn = connect(":memory:")
    add_node(conn, "pending", status="pending-verification")
    add_node(conn, "dead", hb=0.0)
    submit_job(conn, "echo-model", "p", now=1000.0)
    assert poll_for_work(conn, "pending", now=1001.0) is None
    assert poll_for_work(conn, "dead", now=1001.0) is None


def test_result_completes_job():
    conn = connect(":memory:")
    add_node(conn, "n1")
    jid = submit_job(conn, "echo-model", "p", now=1000.0)
    poll_for_work(conn, "n1", now=1001.0)
    submit_result(conn, Result(job_id=jid, placeholdered_completion="done!",
                               node_id="n1", node_signature="sig",
                               completed_at=1002.0), now=1002.0)
    st = job_status(conn, jid)
    assert st["state"] == "done"
    assert st["completions"][0]["completion"] == "done!"


def test_timeout_requeues_then_fails_after_max_attempts():
    conn = connect(":memory:")
    add_node(conn, "n1")
    jid = submit_job(conn, "echo-model", "p", timeout_s=10, now=1000.0)
    for attempt in range(3):
        assert poll_for_work(conn, "n1", now=1001.0 + attempt) is not None
        requeued = check_timeouts(conn, now=2000.0 + attempt)
        if attempt < 2:
            assert requeued == [jid]           # TR-14: reassignable
        else:
            assert requeued == []
    assert job_status(conn, jid)["state"] == "failed"


def test_redundant_job_assigned_to_two_distinct_nodes():
    conn = connect(":memory:")
    add_node(conn, "n1")
    add_node(conn, "n2")
    jid = submit_job(conn, "echo-model", "p", redundant=True, now=1000.0)
    j1 = poll_for_work(conn, "n1", now=1001.0)
    j2 = poll_for_work(conn, "n2", now=1001.0)
    assert j1.job_id == jid and j2.job_id == jid
    assert poll_for_work(conn, "n1", now=1002.0) is None  # never twice to same node
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.dispatch'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/dispatch.py
"""§7.3: pull dispatch (CD1 — nodes dial out; the orchestrator never dials in).
Jobs are idempotent and node-agnostic (TR-14). Selection: capability filter →
reputation → least-loaded (TR-13)."""

import hashlib
import json
import time
import uuid

from cgn.contract import Job, Result
from cgn.registry import alive_nodes

MAX_ATTEMPTS = 3


def _sig(job_id: str, model_spec: str, prompt: str) -> str:
    return hashlib.sha256(f"{job_id}|{model_spec}|{prompt}".encode()).hexdigest()


def submit_job(conn, model_spec: str, placeholdered_prompt: str, params: dict | None = None,
               redundant: bool = False, timeout_s: float = 120.0, kind: str = "inference",
               canary_nonce: str | None = None, node_hint: str | None = None,
               now: float | None = None) -> str:
    now = time.time() if now is None else now
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO jobs (job_id, kind, model_spec, prompt, params, redundant, "
        "canary_nonce, created_at, timeout_s) VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, kind, model_spec, placeholdered_prompt,
         json.dumps({**(params or {}), **({"node_hint": node_hint} if node_hint else {})}),
         int(redundant), canary_nonce, now, timeout_s))
    conn.commit()
    return job_id


def _node_can_serve(node_row, model_spec: str) -> bool:
    caps = json.loads(node_row["capabilities"])
    return model_spec in caps.get("models", [])


def poll_for_work(conn, node_id: str, now: float | None = None) -> Job | None:
    now = time.time() if now is None else now
    node = conn.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    if node is None or node["status"] != "eligible":
        # canaries are the one exception: pending nodes may take their own canary
        if node is None:
            return None
    alive = {r["node_id"] for r in alive_nodes(conn, now=now)}
    if node_id not in alive:
        return None

    for job in conn.execute(
            "SELECT * FROM jobs WHERE state IN ('queued','assigned') "
            "ORDER BY created_at").fetchall():
        params = json.loads(job["params"])
        hint = params.get("node_hint")
        if hint is not None and hint != node_id:
            continue
        if hint is None and node["status"] != "eligible":
            continue
        if not _node_can_serve(node, job["model_spec"]):
            continue
        assigned = conn.execute("SELECT * FROM assignments WHERE job_id=? AND state='assigned'",
                                (job["job_id"],)).fetchall()
        ever = conn.execute("SELECT node_id FROM assignments WHERE job_id=?",
                            (job["job_id"],)).fetchall()
        if node_id in {r["node_id"] for r in ever}:
            continue  # never the same node twice (redundancy independence)
        wanted = 2 if job["redundant"] else 1
        if len(assigned) >= wanted:
            continue
        conn.execute("INSERT INTO assignments (job_id, node_id, assigned_at) VALUES (?,?,?)",
                     (job["job_id"], node_id, now))
        conn.execute("UPDATE jobs SET state='assigned' WHERE job_id=?", (job["job_id"],))
        conn.commit()
        return Job(job_id=job["job_id"], model_spec=job["model_spec"],
                   placeholdered_prompt=job["prompt"], params=params,
                   issued_at=now, timeout=job["timeout_s"],
                   job_signature=_sig(job["job_id"], job["model_spec"], job["prompt"]))
    return None


def submit_result(conn, result: Result, now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "UPDATE assignments SET state='done', completion=?, signature=?, completed_at=? "
        "WHERE job_id=? AND node_id=?",
        (result.placeholdered_completion, result.node_signature, now,
         result.job_id, result.node_id))
    job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (result.job_id,)).fetchone()
    done = conn.execute("SELECT COUNT(*) c FROM assignments WHERE job_id=? AND state='done'",
                        (result.job_id,)).fetchone()["c"]
    wanted = 2 if job["redundant"] else 1
    if done >= wanted:
        conn.execute("UPDATE jobs SET state='done' WHERE job_id=?", (result.job_id,))
    conn.commit()


def check_timeouts(conn, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    requeued: list[str] = []
    rows = conn.execute(
        "SELECT a.job_id, a.node_id, a.assigned_at, j.timeout_s, j.attempts "
        "FROM assignments a JOIN jobs j USING (job_id) "
        "WHERE a.state='assigned' AND j.state='assigned'").fetchall()
    for r in rows:
        if now - r["assigned_at"] <= r["timeout_s"]:
            continue
        conn.execute("UPDATE assignments SET state='timeout' WHERE job_id=? AND node_id=?",
                     (r["job_id"], r["node_id"]))
        attempts = r["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            conn.execute("UPDATE jobs SET state='failed', attempts=? WHERE job_id=?",
                         (attempts, r["job_id"]))
        else:
            conn.execute("UPDATE jobs SET state='queued', attempts=? WHERE job_id=?",
                         (attempts, r["job_id"]))
            requeued.append(r["job_id"])
    conn.commit()
    return requeued


def job_status(conn, job_id: str) -> dict:
    job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    comps = conn.execute(
        "SELECT node_id, completion, signature FROM assignments "
        "WHERE job_id=? AND state='done' ORDER BY completed_at", (job_id,)).fetchall()
    return {"state": job["state"],
            "completions": [{"node_id": c["node_id"], "completion": c["completion"],
                             "signature": c["signature"]} for c in comps]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_dispatch.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/dispatch.py tests/cgn/test_dispatch.py
git commit -m "feat(cgn): pull dispatch — capability match, redundancy slots, timeout reassignment (TR-13/14)"
```

---

### Task 6: Orchestrator app — HTTP surface + canary attestation (TR-24)

**Files:**
- Create: `cgn/orchestrator_app.py`
- Test: `tests/cgn/test_orchestrator_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app(db_path: str = ":memory:") -> FastAPI` exposing:
  - `POST /enroll` (EnrollRequest → EnrollResponse; 400 with `{"reason": …}` on EnrollmentError). On success **immediately queues a canary job** pinned to the node (`node_hint`), prompt `f"Repeat exactly: {nonce}"`, `kind="canary"`, `canary_nonce=nonce` (uuid4 hex), against the node's first declared model.
  - `POST /heartbeat` (HeartbeatRequest → HeartbeatResponse).
  - `POST /nodes/{node_id}/poll` → Job JSON or 204.
  - `POST /results` (Result). If the job is a canary: nonce present in completion → node status `eligible`; absent → node status stays `pending-verification` and the assignment is closed. Regular results go to `dispatch.submit_result`.
  - `POST /jobs` `{model_spec, placeholdered_prompt, params?, redundant?, timeout_s?}` → `{job_id}`.
  - `GET /jobs/{job_id}` → `job_status` dict plus `job_id`.
  - `GET /nodes/{node_id}/pubkey` → `{node_id, public_key}`.
  - `POST /admin/check_timeouts` → `{requeued: [...]}` (called by tests and a timer in production).
  - `app.state.conn` is the sqlite connection (used by tests and Plan 4).
  Helper for tests/tools: `seed_operator(conn, operator_id, email) -> None` and route `POST /operators/{operator_id}/tokens` → `{token}` (Plan 4 replaces this with the real consent flow; here the operator must already exist).

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_orchestrator_app.py
from fastapi.testclient import TestClient

from cgn.node.identity import NodeIdentity
from cgn.orchestrator_app import create_app, seed_operator

CAPS = {"models": ["echo-model"], "max_context": 4096, "throughput_hint": "laptop"}


def make_client():
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    return TestClient(app)


def enroll(client, ident):
    token = client.post("/operators/op1/tokens").json()["token"]
    r = client.post("/enroll", json={
        "enrollment_token": token, "node_public_key": ident.public_key_hex,
        "capabilities": CAPS, "endpoint_info": "outbound-only"})
    assert r.status_code == 200
    return r.json()["node_id"]


def hb(client, ident, node_id, load=0):
    return client.post("/heartbeat", json={
        "node_id": node_id, "signature": ident.sign(f"hb|{node_id}|{load}"),
        "current_load": load})


def test_enroll_then_canary_flips_eligibility(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    assert hb(client, ident, node_id).json()["dispatch_eligible"] is False

    # node polls: gets its canary even while pending
    job = client.post(f"/nodes/{node_id}/poll").json()
    assert "Repeat exactly:" in job["placeholdered_prompt"]
    nonce = job["placeholdered_prompt"].split(": ")[1]
    completion = f"Sure: {nonce}"
    client.post("/results", json={
        "job_id": job["job_id"], "placeholdered_completion": completion,
        "node_id": node_id, "node_signature": ident.sign(f"{job['job_id']}|{completion}"),
        "completed_at": 1.0})
    assert hb(client, ident, node_id).json()["dispatch_eligible"] is True  # TR-24


def test_failed_canary_keeps_node_ineligible(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    hb(client, ident, node_id)
    job = client.post(f"/nodes/{node_id}/poll").json()
    client.post("/results", json={
        "job_id": job["job_id"], "placeholdered_completion": "no idea",
        "node_id": node_id, "node_signature": ident.sign(f"{job['job_id']}|no idea"),
        "completed_at": 1.0})
    assert hb(client, ident, node_id).json()["dispatch_eligible"] is False


def test_job_round_trip_over_http(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    hb(client, ident, node_id)
    canary = client.post(f"/nodes/{node_id}/poll").json()
    nonce = canary["placeholdered_prompt"].split(": ")[1]
    client.post("/results", json={
        "job_id": canary["job_id"], "placeholdered_completion": nonce,
        "node_id": node_id, "node_signature": ident.sign(f"{canary['job_id']}|{nonce}"),
        "completed_at": 1.0})
    hb(client, ident, node_id)

    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": "⟦PERSON_1⟧ owes rent"}).json()["job_id"]
    job = client.post(f"/nodes/{node_id}/poll").json()
    assert job["job_id"] == jid
    completion = f"Summary: {job['placeholdered_prompt']}"
    client.post("/results", json={
        "job_id": jid, "placeholdered_completion": completion, "node_id": node_id,
        "node_signature": ident.sign(f"{jid}|{completion}"), "completed_at": 2.0})
    status = client.get(f"/jobs/{jid}").json()
    assert status["state"] == "done"
    assert status["completions"][0]["completion"] == completion
    pub = client.get(f"/nodes/{node_id}/pubkey").json()
    assert pub["public_key"] == ident.public_key_hex


def test_empty_poll_returns_204(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    hb(client, ident, node_id)
    canary = client.post(f"/nodes/{node_id}/poll").json()
    nonce = canary["placeholdered_prompt"].split(": ")[1]
    client.post("/results", json={
        "job_id": canary["job_id"], "placeholdered_completion": nonce,
        "node_id": node_id, "node_signature": ident.sign(f"{canary['job_id']}|{nonce}"),
        "completed_at": 1.0})
    assert client.post(f"/nodes/{node_id}/poll").status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_orchestrator_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.orchestrator_app'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/orchestrator_app.py
"""Zone 2: PII-blind coordination. Every content field it ever touches is
already placeholdered (TR-16). Nodes dial out; there is no dial-in path (CD1)."""

import uuid

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from cgn import dispatch, registry
from cgn.contract import EnrollRequest, HeartbeatRequest, Result
from cgn.db import connect


def seed_operator(conn, operator_id: str, email: str) -> None:
    import time
    conn.execute("INSERT OR REPLACE INTO operators VALUES (?,?,?)",
                 (operator_id, email, time.time()))
    conn.commit()


class JobSubmit(BaseModel):
    model_spec: str
    placeholdered_prompt: str
    params: dict = {}
    redundant: bool = False
    timeout_s: float = 120.0


def create_app(db_path: str = ":memory:") -> FastAPI:
    app = FastAPI(title="cgn-orchestrator")
    conn = connect(db_path)
    app.state.conn = conn

    @app.post("/operators/{operator_id}/tokens")
    def issue(operator_id: str):
        return {"token": registry.issue_token(conn, operator_id)}

    @app.post("/enroll")
    def enroll(req: EnrollRequest):
        try:
            resp = registry.enroll(conn, req)
        except registry.EnrollmentError as e:
            raise HTTPException(status_code=400, detail={"reason": e.reason})
        nonce = uuid.uuid4().hex
        dispatch.submit_job(conn, req.capabilities.models[0],
                            f"Repeat exactly: {nonce}", kind="canary",
                            canary_nonce=nonce, node_hint=resp.node_id)
        return resp

    @app.post("/heartbeat")
    def heartbeat(req: HeartbeatRequest):
        return registry.heartbeat(conn, req)

    @app.post("/nodes/{node_id}/poll")
    def poll(node_id: str):
        job = dispatch.poll_for_work(conn, node_id)
        if job is None:
            return Response(status_code=204)
        return job

    @app.post("/results")
    def results(result: Result):
        job = conn.execute("SELECT * FROM jobs WHERE job_id=?",
                           (result.job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404)
        if job["kind"] == "canary":
            passed = job["canary_nonce"] in result.placeholdered_completion
            conn.execute("UPDATE assignments SET state='done', completion=?, "
                         "signature=?, completed_at=? WHERE job_id=? AND node_id=?",
                         (result.placeholdered_completion, result.node_signature,
                          result.completed_at, result.job_id, result.node_id))
            conn.execute("UPDATE jobs SET state=? WHERE job_id=?",
                         ("done" if passed else "failed", result.job_id))
            if passed:
                conn.execute("UPDATE nodes SET status='eligible' WHERE node_id=?",
                             (result.node_id,))
            conn.commit()
            return {"ok": True, "canary_passed": passed}
        dispatch.submit_result(conn, result)
        return {"ok": True}

    @app.post("/jobs")
    def submit(job: JobSubmit):
        job_id = dispatch.submit_job(conn, job.model_spec, job.placeholdered_prompt,
                                     params=job.params, redundant=job.redundant,
                                     timeout_s=job.timeout_s)
        return {"job_id": job_id}

    @app.get("/jobs/{job_id}")
    def status(job_id: str):
        return {"job_id": job_id, **dispatch.job_status(conn, job_id)}

    @app.get("/nodes/{node_id}/pubkey")
    def pubkey(node_id: str):
        row = conn.execute("SELECT public_key FROM nodes WHERE node_id=?",
                           (node_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        return {"node_id": node_id, "public_key": row["public_key"]}

    @app.post("/admin/check_timeouts")
    def timeouts():
        return {"requeued": dispatch.check_timeouts(conn)}

    return app
```

Note: `poll_for_work` from Task 5 already lets a `pending-verification` node take only jobs pinned to it via `node_hint` — that is how the canary reaches a not-yet-eligible node.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_orchestrator_app.py -v`
Expected: PASS (4 tests). Also run `uv run pytest tests/cgn -q` — all CGN tests green.

- [ ] **Step 5: Commit**

```bash
git add cgn/orchestrator_app.py tests/cgn/test_orchestrator_app.py
git commit -m "feat(cgn): orchestrator HTTP surface + canary attestation (TR-24)"
```

---

### Task 7: Node worker (TR-23 / §7.4)

**Files:**
- Create: `cgn/node/worker.py`
- Test: `tests/cgn/test_worker.py`

**Interfaces:**
- Consumes: `NodeIdentity`, `cgn.contract`, `Backend` protocol from `cmndr.backends.base`, an injected `httpx.Client`.
- Produces: `NodeWorker(client, backend, key_dir: Path, models: list[str], throughput_hint: str = "laptop")` with `enroll(token: str) -> str` (stores `self.node_id`; sends only the public key), `heartbeat() -> bool` (returns dispatch_eligible), `poll_once() -> str | None` (poll → infer via backend → sign `f"{job_id}|{completion}"` → POST /results; returns handled job_id or None), and `run_forever(poll_interval: float = 2.0)` (loop: heartbeat + poll; used in production, not tests). Also a CLI entry `python -m cgn.node.worker --orchestrator URL --token T --models echo-model --key-dir DIR` wiring `LlamaCppBackend` if available else `EchoBackend`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_worker.py
from fastapi.testclient import TestClient

from cmndr.backends.fake import EchoBackend
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app, seed_operator


def make():
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    client = TestClient(app)
    return app, client


def test_worker_enrolls_passes_canary_and_serves_job(tmp_path):
    app, client = make()
    worker = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    node_id = worker.enroll(token)
    assert node_id.startswith("node-")
    assert worker.heartbeat() is False       # pending until canary

    handled = worker.poll_once()             # canary: EchoBackend echoes the nonce
    assert handled is not None
    assert worker.heartbeat() is True        # TR-24 passed

    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": "⟦PERSON_1⟧ hi"}).json()["job_id"]
    assert worker.poll_once() == jid
    status = client.get(f"/jobs/{jid}").json()
    assert status["state"] == "done"
    assert "⟦PERSON_1⟧" in status["completions"][0]["completion"]


def test_enrollment_traffic_contains_only_public_key(tmp_path, monkeypatch):
    app, client = make()
    sent = []
    original_post = client.post

    def spy_post(url, **kw):
        sent.append((url, kw.get("json")))
        return original_post(url, **kw)

    monkeypatch.setattr(client, "post", spy_post)
    worker = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    worker.enroll(token)
    private_pem = (tmp_path / "node_key.pem").read_text()
    for _, body in sent:
        assert body is None or "PRIVATE" not in str(body)          # TR-23
        assert body is None or private_pem not in str(body)


def test_poll_once_returns_none_when_no_work(tmp_path):
    app, client = make()
    worker = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    worker.enroll(token)
    worker.heartbeat()
    worker.poll_once()                        # canary
    assert worker.poll_once() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.node.worker'`

- [ ] **Step 3: Write minimal implementation**

```python
# cgn/node/worker.py
"""§7.4: the node agent. Sees only placeholdered text — never the map, raw
payload, or customer identity. Dial-out only (CD1). 'Register a GPU' ==
'run this module with a token'."""

import argparse
import time
from pathlib import Path

import httpx

from cgn.contract import Capabilities, EnrollRequest, HeartbeatRequest, Job, Result
from cgn.node.identity import NodeIdentity
from cmndr.backends.base import Backend


class NodeWorker:
    def __init__(self, client: httpx.Client, backend: Backend, key_dir: Path,
                 models: list[str], throughput_hint: str = "laptop") -> None:
        self._client = client
        self._backend = backend
        key_dir = Path(key_dir)
        if (key_dir / "node_key.pem").exists():
            self._ident = NodeIdentity.load(key_dir)
        else:
            self._ident = NodeIdentity.create(key_dir)
        self._models = models
        self._throughput_hint = throughput_hint
        self.node_id: str | None = None
        self._load = 0

    def enroll(self, token: str) -> str:
        req = EnrollRequest(
            enrollment_token=token,
            node_public_key=self._ident.public_key_hex,   # ONLY the public key
            capabilities=Capabilities(models=self._models, max_context=4096,
                                      throughput_hint=self._throughput_hint),
            endpoint_info="outbound-only")
        r = self._client.post("/enroll", json=req.model_dump())
        r.raise_for_status()
        self.node_id = r.json()["node_id"]
        return self.node_id

    def heartbeat(self) -> bool:
        req = HeartbeatRequest(node_id=self.node_id,
                               signature=self._ident.sign(f"hb|{self.node_id}|{self._load}"),
                               current_load=self._load)
        r = self._client.post("/heartbeat", json=req.model_dump())
        r.raise_for_status()
        return bool(r.json()["dispatch_eligible"])

    def poll_once(self) -> str | None:
        r = self._client.post(f"/nodes/{self.node_id}/poll")
        if r.status_code == 204:
            return None
        job = Job.model_validate(r.json())
        self._load = 1
        try:
            completion = self._backend.infer(
                job.placeholdered_prompt,
                max_tokens=job.params.get("max_tokens", 512)).text
        finally:
            self._load = 0
        result = Result(job_id=job.job_id, placeholdered_completion=completion,
                        node_id=self.node_id,
                        node_signature=self._ident.sign(f"{job.job_id}|{completion}"),
                        completed_at=time.time())
        self._client.post("/results", json=result.model_dump()).raise_for_status()
        return job.job_id

    def run_forever(self, poll_interval: float = 2.0) -> None:
        while True:
            self.heartbeat()
            if self.poll_once() is None:
                time.sleep(poll_interval)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--orchestrator", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--models", nargs="+", default=["mistral-7b-instruct-v0.2.Q4_K_M"])
    p.add_argument("--key-dir", default="~/.cmndr-node")
    args = p.parse_args()

    from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                         llamacpp_available)
    from cmndr.backends.fake import EchoBackend
    backend = (LlamaCppBackend(DEFAULT_MODEL_PATH)
               if llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()
               else EchoBackend())
    client = httpx.Client(base_url=args.orchestrator, timeout=300)
    worker = NodeWorker(client, backend, Path(args.key_dir).expanduser(), args.models)
    worker.enroll(args.token)
    worker.run_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_worker.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add cgn/node/worker.py tests/cgn/test_worker.py
git commit -m "feat(cgn): node worker — enroll, heartbeat, pull, sign, canary (TR-23/24, CD1)"
```

---

### Task 8: Redundant verification + reputation ejection (TR-18 / TR-19)

**Files:**
- Create: `cgn/reputation.py`
- Modify: `cgn/dispatch.py` (call verification when a redundant job completes; consult floor in `poll_for_work`; penalize on timeout)
- Test: `tests/cgn/test_reputation.py`

**Interfaces:**
- Consumes: `cgn.db`, dispatch internals.
- Produces: `cgn/reputation.py` with `PASS_DELTA = 0.05`, `FAIL_DELTA = -0.2`, `FLOOR = 0.3`, `adjust(conn, node_id, delta) -> float` (clamped to [0,1]; sets status `ejected` when < FLOOR), `compare_redundant(conn, job_id) -> bool` (normalized-whitespace equality of the two completions; agreement → both nodes `+PASS_DELTA`, job stays `done`; divergence → both `-FAIL_DELTA` applied as `FAIL_DELTA`, job state set to `divergent`, returns False). Dispatch changes: `submit_result` calls `compare_redundant` when a redundant job reaches 2 results; `check_timeouts` applies `adjust(conn, node_id, FAIL_DELTA)` for the timed-out node; `poll_for_work` skips nodes with status `ejected` (already true — only `eligible` receive non-hinted work) and orders candidates by reputation (already true).

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_reputation.py
from cgn.contract import Result
from cgn.db import connect
from cgn.dispatch import check_timeouts, job_status, poll_for_work, submit_job, submit_result
from cgn.reputation import FLOOR, adjust
from tests.cgn.test_dispatch import add_node


def rep(conn, node_id):
    return conn.execute("SELECT reputation, status FROM nodes WHERE node_id=?",
                        (node_id,)).fetchone()


def run_redundant(conn, completions: dict[str, str]):
    jid = submit_job(conn, "echo-model", "p", redundant=True, now=1000.0)
    for node_id, completion in completions.items():
        poll_for_work(conn, node_id, now=1001.0)
    for node_id, completion in completions.items():
        submit_result(conn, Result(job_id=jid, placeholdered_completion=completion,
                                   node_id=node_id, node_signature="s",
                                   completed_at=1002.0), now=1002.0)
    return jid


def test_agreement_rewards_both():
    conn = connect(":memory:")
    add_node(conn, "n1"); add_node(conn, "n2")
    jid = run_redundant(conn, {"n1": "same answer", "n2": "same  answer"})
    assert job_status(conn, jid)["state"] == "done"
    assert rep(conn, "n1")["reputation"] > 1.0 - 1e-9 or rep(conn, "n1")["reputation"] == 1.0


def test_divergence_flags_job_and_penalizes_both():
    conn = connect(":memory:")
    add_node(conn, "n1"); add_node(conn, "n2")
    jid = run_redundant(conn, {"n1": "the real answer", "n2": "garbage to farm credit"})
    assert job_status(conn, jid)["state"] == "divergent"          # TR-18
    assert rep(conn, "n1")["reputation"] == 0.8
    assert rep(conn, "n2")["reputation"] == 0.8


def test_node_below_floor_is_ejected_and_stops_receiving_work():
    conn = connect(":memory:")
    add_node(conn, "n1", reputation=0.4)
    adjust(conn, "n1", -0.2)                                       # 0.2 < FLOOR
    assert rep(conn, "n1")["status"] == "ejected"                  # TR-19
    submit_job(conn, "echo-model", "p", now=1000.0)
    assert poll_for_work(conn, "n1", now=1001.0) is None


def test_timeout_penalizes_the_silent_node():
    conn = connect(":memory:")
    add_node(conn, "n1")
    submit_job(conn, "echo-model", "p", timeout_s=10, now=1000.0)
    poll_for_work(conn, "n1", now=1001.0)
    check_timeouts(conn, now=2000.0)
    assert rep(conn, "n1")["reputation"] == 0.8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_reputation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cgn.reputation'`

- [ ] **Step 3: Write the reputation module**

```python
# cgn/reputation.py
"""§7.5: statistical honesty enforcement (TR-18/19). Catches consistent bad
actors; NOT per-job cryptographic proof (TR-20 — documented limit). With two
diverging results we can't know which node lied, so both are penalized; the
consistent liar hits the floor first."""

PASS_DELTA = 0.05
FAIL_DELTA = -0.2
FLOOR = 0.3


def adjust(conn, node_id: str, delta: float) -> float:
    row = conn.execute("SELECT reputation FROM nodes WHERE node_id=?",
                       (node_id,)).fetchone()
    new = max(0.0, min(1.0, row["reputation"] + delta))
    status_sql = ", status='ejected'" if new < FLOOR else ""
    conn.execute(f"UPDATE nodes SET reputation=?{status_sql} WHERE node_id=?",
                 (new, node_id))
    conn.commit()
    return new


def _normalize(text: str) -> str:
    return " ".join(text.split())


def compare_redundant(conn, job_id: str) -> bool:
    rows = conn.execute(
        "SELECT node_id, completion FROM assignments WHERE job_id=? AND state='done'",
        (job_id,)).fetchall()
    if len(rows) < 2:
        return True
    a, b = rows[0], rows[1]
    if _normalize(a["completion"]) == _normalize(b["completion"]):
        adjust(conn, a["node_id"], PASS_DELTA)
        adjust(conn, b["node_id"], PASS_DELTA)
        return True
    adjust(conn, a["node_id"], FAIL_DELTA)
    adjust(conn, b["node_id"], FAIL_DELTA)
    conn.execute("UPDATE jobs SET state='divergent' WHERE job_id=?", (job_id,))
    conn.commit()
    return False
```

- [ ] **Step 4: Wire into dispatch**

In `cgn/dispatch.py`:

1. Add import at top: `from cgn import reputation`
2. In `submit_result`, replace the block

```python
    if done >= wanted:
        conn.execute("UPDATE jobs SET state='done' WHERE job_id=?", (result.job_id,))
    conn.commit()
```

with

```python
    if done >= wanted:
        conn.execute("UPDATE jobs SET state='done' WHERE job_id=?", (result.job_id,))
        conn.commit()
        if job["redundant"]:
            reputation.compare_redundant(conn, result.job_id)   # TR-18
        return
    conn.commit()
```

3. In `check_timeouts`, directly after the line marking the assignment `timeout` (`conn.execute("UPDATE assignments SET state='timeout' ...`), add:

```python
        reputation.adjust(conn, r["node_id"], reputation.FAIL_DELTA)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/cgn/test_reputation.py tests/cgn/test_dispatch.py -v`
Expected: PASS (all — dispatch tests still green because non-redundant paths are unchanged)

- [ ] **Step 6: Commit**

```bash
git add cgn/reputation.py cgn/dispatch.py tests/cgn/test_reputation.py
git commit -m "feat(cgn): redundant verification, divergence penalty, reputation ejection (TR-18/19)"
```

---

### Task 9: CGNProvider — device-side dispatcher with signature-verify-before-restore (TR-17)

**Files:**
- Create: `cmndr/providers/cgn.py`
- Test: `tests/cgn/test_provider.py`

**Interfaces:**
- Consumes: `Provider` protocol (`infer(payload: PlaceholderedPayload, max_tokens=512) -> str`), orchestrator HTTP surface, `cgn.node.identity.verify`.
- Produces: `CGNProvider(client: httpx.Client, model_spec: str, poll_interval: float = 0.05, timeout: float = 300.0, redundant: bool = False)` implementing the Provider protocol. Behavior: POST /jobs → poll GET /jobs/{id} until `done`/`failed`/`divergent`; on `done`, fetch the completing node's pubkey and `verify(pubkey, f"{job_id}|{completion}", signature)`; invalid signature → raise `ResultSignatureInvalid`; `failed`/`divergent` → raise `JobFailed(state)`; timeout → `TimeoutError`. Exceptions defined in the module.

- [ ] **Step 1: Write the failing test**

```python
# tests/cgn/test_provider.py
import pytest
from fastapi.testclient import TestClient

from cmndr.backends.fake import EchoBackend
from cmndr.providers.cgn import CGNProvider, JobFailed, ResultSignatureInvalid
from cmndr.types import PlaceholderedPayload
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app, seed_operator


def make_network(tmp_path, n_nodes=1):
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    client = TestClient(app)
    workers = []
    for i in range(n_nodes):
        w = NodeWorker(client, EchoBackend(), tmp_path / f"n{i}", models=["echo-model"])
        token = client.post("/operators/op1/tokens").json()["token"]
        w.enroll(token)
        w.heartbeat()
        w.poll_once()   # canary
        w.heartbeat()
        workers.append(w)
    return app, client, workers


def infer_with_worker_pump(provider, payload, workers, client):
    """Drive the async loop deterministically: submit, let workers pump, poll."""
    import threading
    done = {}

    def run():
        done["text"] = provider.infer(payload)

    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        for w in workers:
            w.poll_once()
        t.join(timeout=0.02)
        if not t.is_alive():
            break
    t.join(timeout=5)
    assert not t.is_alive(), "provider.infer never returned"
    return done["text"]


def test_provider_round_trip_with_signature_verification(tmp_path):
    app, client, workers = make_network(tmp_path)
    provider = CGNProvider(client, "echo-model")
    payload = PlaceholderedPayload("r1", "⟦PERSON_1⟧ owes ⟦ORG_1⟧ rent", {"PERSON": 1, "ORG": 1})
    text = infer_with_worker_pump(provider, payload, workers, client)
    assert "⟦PERSON_1⟧" in text     # completion still placeholdered


def test_tampered_result_is_rejected(tmp_path):
    app, client, workers = make_network(tmp_path)
    provider = CGNProvider(client, "echo-model", timeout=5)
    payload = PlaceholderedPayload("r2", "⟦PERSON_1⟧ hi", {"PERSON": 1})

    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": payload.text}).json()["job_id"]
    workers[0].poll_once()
    # tamper with the stored completion after signing
    app.state.conn.execute(
        "UPDATE assignments SET completion='FORGED' WHERE job_id=?", (jid,))
    app.state.conn.commit()
    with pytest.raises(ResultSignatureInvalid):
        provider._await_verified(jid)          # TR-17: reject before restore


def test_failed_job_raises(tmp_path):
    app, client, workers = make_network(tmp_path)
    provider = CGNProvider(client, "echo-model", timeout=5)
    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": "p"}).json()["job_id"]
    app.state.conn.execute("UPDATE jobs SET state='failed' WHERE job_id=?", (jid,))
    app.state.conn.commit()
    with pytest.raises(JobFailed):
        provider._await_verified(jid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cgn/test_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.providers.cgn'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/providers/cgn.py
"""A1/A2: the on-device dispatcher behind the Provider seam. Submits only
placeholdered payloads (the Pipeline guarantees this structurally, TR-7) and
verifies the node's signature BEFORE anything is restored (TR-17)."""

import time

import httpx

from cgn.node.identity import verify
from cmndr.types import PlaceholderedPayload


class ResultSignatureInvalid(Exception):
    pass


class JobFailed(Exception):
    pass


class CGNProvider:
    def __init__(self, client: httpx.Client, model_spec: str,
                 poll_interval: float = 0.05, timeout: float = 300.0,
                 redundant: bool = False) -> None:
        self._client = client
        self._model_spec = model_spec
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._redundant = redundant

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str:
        r = self._client.post("/jobs", json={
            "model_spec": self._model_spec,
            "placeholdered_prompt": payload.text,
            "params": {"max_tokens": max_tokens},
            "redundant": self._redundant})
        r.raise_for_status()
        return self._await_verified(r.json()["job_id"])

    def _await_verified(self, job_id: str) -> str:
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            status = self._client.get(f"/jobs/{job_id}").json()
            state = status["state"]
            if state == "done":
                comp = status["completions"][0]
                pub = self._client.get(f"/nodes/{comp['node_id']}/pubkey").json()["public_key"]
                if not verify(pub, f"{job_id}|{comp['completion']}", comp["signature"]):
                    raise ResultSignatureInvalid(job_id)
                return comp["completion"]
            if state in ("failed", "divergent"):
                raise JobFailed(state)
            time.sleep(self._poll_interval)
        raise TimeoutError(job_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cgn/test_provider.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add cmndr/providers/cgn.py tests/cgn/test_provider.py
git commit -m "feat(cmndr): CGN provider — signed results verified before restore (TR-17)"
```

---

### Task 10: End-to-end integration — full pipeline over CGN, churn, PII-blindness (TR-14/15/16)

**Files:**
- Test: `tests/cgn/test_integration.py`

**Interfaces:**
- Consumes: everything. No new production code — this task is the proof.

- [ ] **Step 1: Write the integration test**

```python
# tests/cgn/test_integration.py
"""The loop the whole architecture exists to prove: raw PII in → routed →
anonymized → previewed → dispatched to an untrusted node → signed result →
verified → restored — with node churn, and with the orchestrator provably
PII-blind (a full dump of Zone-2 state contains no raw entity)."""

import threading

from fastapi.testclient import TestClient

from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.fake import EchoBackend
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.cgn import CGNProvider
from cmndr.router import ThresholdRouter
from cmndr.types import RequestItem
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app, seed_operator

RAW_VALUES = ["Jane Smith", "Acme Corp", "jane@acme.example"]
DETECTOR = {"Jane Smith": "PERSON", "Acme Corp": "ORG", "jane@acme.example": "EMAIL_ADDRESS"}


def make_network(tmp_path, n_nodes):
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    client = TestClient(app)
    workers = []
    for i in range(n_nodes):
        w = NodeWorker(client, EchoBackend(), tmp_path / f"n{i}", models=["echo-model"])
        token = client.post("/operators/op1/tokens").json()["token"]
        w.enroll(token)
        w.heartbeat()
        w.poll_once()
        w.heartbeat()
        workers.append(w)
    return app, client, workers


def make_pipeline(client, tmp_path):
    return Pipeline(
        router=ThresholdRouter(),
        anonymizer=Anonymizer(WordlistDetector(DETECTOR)),
        local_backend=EchoBackend(),
        provider=CGNProvider(client, "echo-model", timeout=30),
        audit=AuditLog(tmp_path / "audit.jsonl"))


def pump(workers, stop):
    while not stop.is_set():
        for w in workers:
            try:
                w.poll_once()
            except Exception:
                pass


def test_full_loop_with_churn_and_pii_blind_orchestrator(tmp_path):
    app, client, workers = make_network(tmp_path, n_nodes=3)
    pipe = make_pipeline(client, tmp_path)

    stop = threading.Event()
    pumper = threading.Thread(target=pump, args=(workers, stop))
    pumper.start()
    try:
        items = [RequestItem(f"r{i}", "summarize",
                             f"Doc {i}: Jane Smith of Acme Corp (jane@acme.example) owes rent.",
                             sensitivity_hint="high")
                 for i in range(10)]
        responses = []
        for i, item in enumerate(items):
            if i == 5:
                workers.pop()      # churn: a node vanishes mid-batch (TR-14)
            responses.append(pipe.process_item(item, approve=lambda p: p.accept()))
    finally:
        stop.set()
        pumper.join(timeout=10)

    # every response restored: raw values back, no placeholders left
    assert len(responses) == 10
    for resp in responses:
        assert resp.route == "escalate"
        assert "Jane Smith" in resp.text
        assert "⟦" not in resp.text
        assert resp.restoration_anomalies == []

    # TR-15/16: dump ALL Zone-2 state; no raw entity value anywhere
    conn = app.state.conn
    dump = "\n".join("|".join(str(v) for v in row)
                     for table in ("jobs", "assignments", "nodes")
                     for row in conn.execute(f"SELECT * FROM {table}").fetchall())
    for raw in RAW_VALUES:
        assert raw not in dump

    # audit shows the escalations, placeholdered (TR-8 over the network)
    audit = AuditLog(tmp_path / "audit.jsonl").entries()
    assert sum(1 for e in audit if e["kind"] == "escalation") == 10
```

- [ ] **Step 2: Run the integration test**

Run: `uv run pytest tests/cgn/test_integration.py -v`
Expected: PASS (1 test, may take a few seconds)

- [ ] **Step 3: Run the entire repo suite**

Run: `uv run pytest tests -q --ignore=tests/stress_test.py`
Expected: PASS — legacy suites (`test_orchestrator.py`, `test_node_agent.py`, `test_anonymizer.py`), `tests/cmndr`, and `tests/cgn` all green together.

- [ ] **Step 4: Commit**

```bash
git add tests/cgn/test_integration.py
git commit -m "test(cgn): end-to-end loop — churn survival + PII-blind Zone 2 (TR-14/15/16)"
```

---

## Self-Review

**1. Spec coverage (this plan = CGN A→D + A1/A2):**
- TR-12 (register + liveness) → Task 4. TR-13 (capability match) → Task 5. TR-14 (reassignment) → Tasks 5, 10. ✅
- TR-15/16 (boundary + PII-blindness) → Task 10 dump assertion. ✅
- TR-17 (signed results verified on-device before restore) → Tasks 3, 9. ✅
- TR-18/19 (redundancy, divergence, ejection) → Task 8. ✅
- TR-20 (documented statistical-integrity scope) → module docstring in `cgn/reputation.py` + spec §2; no code needed. ✅
- TR-22 (single-use/time-limited/account-bound tokens) → Task 4. TR-23 (node-side keys) → Tasks 3, 7. TR-24 (canary) → Tasks 6, 7. ✅
- TR-21 consent gate enforced in `enroll` (Task 4); the operator-facing flow itself is Plan 4. TR-25 (dashboard) is Plan 4. TR-28's schema is frozen here (Task 1); stream producer/consumer is Plan 4. ✅
- CD1 (outbound-only) → pull dispatch, Tasks 5–7. CD2 (via orchestrator) → Task 9. CD3 (sampling rate) → `redundant` flag on submit; rate is a caller choice (`CGNProvider(redundant=...)`). CD5 (no payment) → Task 2 schema has no ledger. ✅

**2. Placeholder scan:** every step carries complete code, commands, expected output; no TBD/TODO. ✅

**3. Type consistency:** `EnrollRequest/Result/Job` field names identical across contract (Task 1), registry (4), dispatch (5), app (6), worker (7), provider (9); signature message format `f"{job_id}|{completion}"` identical in worker, provider, and tamper test; heartbeat message `f"hb|{node_id}|{load}"` identical in registry and worker; `job_status` dict keys (`state`, `completions[].node_id/completion/signature`) match provider usage. `add_node` test helper is imported by Task 8's test from `tests/cgn/test_dispatch.py` — defined there in Task 5. ✅
