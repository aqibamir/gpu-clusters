"""
Unit tests for the orchestrator — uses an in-memory SQLite DB.
"""

import os
import time
import sqlite3
import pytest

os.environ["DB_PATH"] = ":memory:"

from orchestrator.db import init_db
from orchestrator.registry import (
    register_node,
    record_heartbeat,
    online_nodes,
    update_reputation,
    get_node,
)
from orchestrator.scheduler import pick_node


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _node_payload(node_id: str, model: str = "mistral-7b-q4_k_m") -> dict:
    return {
        "node_id": node_id,
        "chip": "apple_m3_pro",
        "ram_gb": 36,
        "max_model": model,
        "throughput_class": "medium",
        "models_loaded": [model],
        "base_url": f"http://localhost:800{node_id[-1]}",
    }


# --- Registry ---

def test_register_and_retrieve(conn):
    register_node(conn, _node_payload("node-1"))
    node = get_node(conn, "node-1")
    assert node is not None
    assert node.node_id == "node-1"
    assert "mistral-7b-q4_k_m" in node.models_loaded


def test_heartbeat_updates_timestamp(conn):
    register_node(conn, _node_payload("node-2"))
    old_ts = get_node(conn, "node-2").last_heartbeat
    time.sleep(0.05)
    record_heartbeat(conn, "node-2")
    new_ts = get_node(conn, "node-2").last_heartbeat
    assert new_ts >= old_ts


def test_heartbeat_unknown_node_returns_false(conn):
    assert record_heartbeat(conn, "nonexistent") is False


def test_online_nodes_excludes_stale(conn):
    register_node(conn, _node_payload("node-3"))
    # Manually backdating last_heartbeat to simulate a stale node.
    conn.execute(
        "UPDATE nodes SET last_heartbeat = ? WHERE node_id = 'node-3'",
        (int(time.time()) - 120,),
    )
    conn.commit()
    online = online_nodes(conn)
    assert all(n.node_id != "node-3" for n in online)


def test_online_nodes_includes_fresh(conn):
    register_node(conn, _node_payload("node-4"))
    record_heartbeat(conn, "node-4")
    online = [n.node_id for n in online_nodes(conn)]
    assert "node-4" in online


# --- Scheduler ---

def test_pick_node_returns_capable_node(conn):
    register_node(conn, _node_payload("node-5"))
    record_heartbeat(conn, "node-5")
    node = pick_node("mistral-7b-q4_k_m", conn)
    assert node is not None
    assert node.node_id == "node-5"


def test_pick_node_returns_none_when_no_match(conn):
    register_node(conn, _node_payload("node-6", model="mistral-7b-q4_k_m"))
    record_heartbeat(conn, "node-6")
    node = pick_node("llama-70b-q4_k_m", conn)  # model not loaded
    assert node is None


def test_pick_node_skips_low_reputation(conn):
    register_node(conn, _node_payload("node-7"))
    record_heartbeat(conn, "node-7")
    update_reputation(conn, "node-7", 0.5)
    node = pick_node("mistral-7b-q4_k_m", conn)
    assert node is None


def test_reputation_clamped(conn):
    register_node(conn, _node_payload("node-8"))
    update_reputation(conn, "node-8", 1.5)
    assert get_node(conn, "node-8").reputation == 1.0
    update_reputation(conn, "node-8", -0.5)
    assert get_node(conn, "node-8").reputation == 0.0
