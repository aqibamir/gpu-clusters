"""§7.3: pull dispatch (CD1 — nodes dial out; the orchestrator never dials in).
Jobs are idempotent and node-agnostic (TR-14). Selection: capability filter →
reputation → least-loaded (TR-13)."""

import hashlib
import json
import time
import uuid

from cgn import reputation
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
    stored = {**(params or {})}
    if node_hint:
        stored["node_hint"] = node_hint
    conn.execute(
        "INSERT INTO jobs (job_id, kind, model_spec, prompt, params, redundant, "
        "canary_nonce, created_at, timeout_s) VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, kind, model_spec, placeholdered_prompt, json.dumps(stored),
         int(redundant), canary_nonce, now, timeout_s))
    conn.commit()
    return job_id


def _node_can_serve(node_row, model_spec: str) -> bool:
    caps = json.loads(node_row["capabilities"])
    return model_spec in caps.get("models", [])


def poll_for_work(conn, node_id: str, now: float | None = None) -> Job | None:
    now = time.time() if now is None else now
    node = conn.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    if node is None:
        return None
    if node_id not in {r["node_id"] for r in alive_nodes(conn, now=now)}:
        return None

    for job in conn.execute(
            "SELECT * FROM jobs WHERE state IN ('queued','assigned') "
            "ORDER BY created_at").fetchall():
        hint = json.loads(job["params"]).get("node_hint")
        if hint is not None:
            if hint != node_id:
                continue  # pinned elsewhere (canary for another node)
        elif node["status"] != "eligible":
            continue      # unhinted work is for eligible nodes only

        if not _node_can_serve(node, job["model_spec"]):
            continue

        # never hand the same job to a node that already holds/finished it
        # (redundancy independence); a timed-out node may be re-assigned
        held = conn.execute(
            "SELECT COUNT(*) c FROM assignments WHERE job_id=? AND node_id=? "
            "AND state IN ('assigned','done')", (job["job_id"], node_id)).fetchone()["c"]
        if held:
            continue
        active = conn.execute(
            "SELECT COUNT(*) c FROM assignments WHERE job_id=? AND state='assigned'",
            (job["job_id"],)).fetchone()["c"]
        wanted = 2 if job["redundant"] else 1
        if active >= wanted:
            continue

        conn.execute(
            "INSERT OR REPLACE INTO assignments (job_id, node_id, assigned_at, state) "
            "VALUES (?,?,?,'assigned')", (job["job_id"], node_id, now))
        conn.execute("UPDATE jobs SET state='assigned' WHERE job_id=?", (job["job_id"],))
        conn.commit()
        return Job(job_id=job["job_id"], model_spec=job["model_spec"],
                   placeholdered_prompt=job["prompt"], params=json.loads(job["params"]),
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
        if job["redundant"]:
            reputation.compare_redundant(conn, result.job_id)   # TR-18
        return
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
        reputation.adjust(conn, r["node_id"], reputation.FAIL_DELTA)
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
