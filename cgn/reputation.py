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
