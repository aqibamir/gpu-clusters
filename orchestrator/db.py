"""
SQLite schema initialisation and a thin connection helper.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(
    __import__("os").environ.get("DB_PATH", "orchestrator.db")
)

_DDL = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id         TEXT PRIMARY KEY,
    chip            TEXT,
    ram_gb          INTEGER,
    max_model       TEXT,
    throughput_class TEXT,
    reputation      REAL    DEFAULT 1.0,
    last_heartbeat  INTEGER DEFAULT 0,
    base_url        TEXT    NOT NULL DEFAULT '',
    models_loaded   TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    status          TEXT    NOT NULL DEFAULT 'queued',
    model           TEXT    NOT NULL,
    prompt          TEXT    NOT NULL,
    result          TEXT,
    restoration_map BLOB,
    assigned_node   TEXT,
    submitted_at    INTEGER NOT NULL,
    completed_at    INTEGER
);

CREATE TABLE IF NOT EXISTS challenges (
    challenge_id    TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    node_id         TEXT,
    passed          INTEGER
);

CREATE TABLE IF NOT EXISTS credits (
    node_id          TEXT    NOT NULL,
    job_id           TEXT    NOT NULL,
    tokens_generated INTEGER NOT NULL,
    latency_ms       REAL    NOT NULL,
    credit_amount    REAL    NOT NULL,
    awarded_at       INTEGER NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()
