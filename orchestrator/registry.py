"""
Node registry — registration, heartbeats, and online-node queries.
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

HEARTBEAT_TTL_SECONDS = 60


@dataclass
class NodeInfo:
    node_id: str
    chip: str
    ram_gb: int
    max_model: str
    throughput_class: str
    reputation: float
    last_heartbeat: int
    base_url: str
    models_loaded: list[str] = field(default_factory=list)
    queue_depth: int = 0  # incremented by scheduler, not persisted

    def can_run(self, model: str) -> bool:
        """True if the node has registered this model or it fits in RAM."""
        return model in self.models_loaded


def register_node(conn: sqlite3.Connection, payload: dict) -> None:
    models_json = json.dumps(payload.get("models_loaded", []))
    conn.execute(
        """
        INSERT INTO nodes (node_id, chip, ram_gb, max_model, throughput_class,
                           reputation, last_heartbeat, base_url, models_loaded)
        VALUES (:node_id, :chip, :ram_gb, :max_model, :throughput_class,
                1.0, :now, :base_url, :models_loaded)
        ON CONFLICT(node_id) DO UPDATE SET
            chip             = excluded.chip,
            ram_gb           = excluded.ram_gb,
            max_model        = excluded.max_model,
            throughput_class = excluded.throughput_class,
            last_heartbeat   = excluded.last_heartbeat,
            base_url         = excluded.base_url,
            models_loaded    = excluded.models_loaded
        """,
        {
            "node_id": payload["node_id"],
            "chip": payload.get("chip", ""),
            "ram_gb": payload.get("ram_gb", 0),
            "max_model": payload.get("max_model", ""),
            "throughput_class": payload.get("throughput_class", "medium"),
            "now": int(time.time()),
            "base_url": payload.get("base_url", ""),
            "models_loaded": models_json,
        },
    )
    conn.commit()


def record_heartbeat(conn: sqlite3.Connection, node_id: str) -> bool:
    cur = conn.execute(
        "UPDATE nodes SET last_heartbeat = ? WHERE node_id = ?",
        (int(time.time()), node_id),
    )
    conn.commit()
    return cur.rowcount > 0


def online_nodes(conn: sqlite3.Connection) -> list[NodeInfo]:
    cutoff = int(time.time()) - HEARTBEAT_TTL_SECONDS
    rows = conn.execute(
        "SELECT * FROM nodes WHERE last_heartbeat >= ?", (cutoff,)
    ).fetchall()
    result = []
    for row in rows:
        result.append(
            NodeInfo(
                node_id=row["node_id"],
                chip=row["chip"],
                ram_gb=row["ram_gb"],
                max_model=row["max_model"],
                throughput_class=row["throughput_class"],
                reputation=row["reputation"],
                last_heartbeat=row["last_heartbeat"],
                base_url=row["base_url"],
                models_loaded=json.loads(row["models_loaded"] or "[]"),
            )
        )
    return result


def update_reputation(
    conn: sqlite3.Connection, node_id: str, reputation: float
) -> None:
    conn.execute(
        "UPDATE nodes SET reputation = ? WHERE node_id = ?",
        (max(0.0, min(1.0, reputation)), node_id),
    )
    conn.commit()


def get_node(conn: sqlite3.Connection, node_id: str) -> Optional[NodeInfo]:
    row = conn.execute(
        "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
    ).fetchone()
    if row is None:
        return None
    return NodeInfo(
        node_id=row["node_id"],
        chip=row["chip"],
        ram_gb=row["ram_gb"],
        max_model=row["max_model"],
        throughput_class=row["throughput_class"],
        reputation=row["reputation"],
        last_heartbeat=row["last_heartbeat"],
        base_url=row["base_url"],
        models_loaded=json.loads(row["models_loaded"] or "[]"),
    )
