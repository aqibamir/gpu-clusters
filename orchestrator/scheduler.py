"""
Scheduler — picks the best available node for a job.
"""

import sqlite3
from typing import Optional

from orchestrator.registry import NodeInfo, online_nodes


def pick_node(model: str, conn: sqlite3.Connection) -> Optional[NodeInfo]:
    """
    Returns the best online node for `model`, or None if no node is available.

    Selection rules (in order):
    1. Node must be online (heartbeat within TTL).
    2. Node must have `model` in its models_loaded list.
    3. Node reputation must be >= 0.8.
    4. Among candidates, prefer lower queue_depth (fewest in-flight jobs).
    """
    candidates = [
        n for n in online_nodes(conn)
        if n.can_run(model) and n.reputation >= 0.8
    ]

    if not candidates:
        return None

    # Prefer nodes that already have the model loaded (they always will here,
    # but this is the hook for future multi-model support).
    loaded = [n for n in candidates if model in n.models_loaded]
    pool = loaded if loaded else candidates

    return min(pool, key=lambda n: n.queue_depth)
