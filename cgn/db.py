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
