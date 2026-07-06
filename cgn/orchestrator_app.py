"""Zone 2: PII-blind coordination. Every content field it ever touches is
already placeholdered (TR-16). Nodes dial out; there is no dial-in path (CD1)."""

import time
import uuid

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from cgn import dispatch, registry
from cgn.contract import EnrollRequest, HeartbeatRequest, Result
from cgn.db import connect


def seed_operator(conn, operator_id: str, email: str) -> None:
    conn.execute("INSERT OR REPLACE INTO operators VALUES (?,?,?)",
                 (operator_id, email, time.time()))
    conn.commit()


class JobSubmit(BaseModel):
    model_spec: str
    placeholdered_prompt: str
    params: dict = {}
    redundant: bool = False
    timeout_s: float = 120.0


class OperatorSignup(BaseModel):
    operator_id: str
    email: str
    consent: bool


def create_app(db_path: str = ":memory:") -> FastAPI:
    app = FastAPI(title="cgn-orchestrator")
    conn = connect(db_path)
    app.state.conn = conn

    @app.post("/operators/{operator_id}/tokens")
    def issue(operator_id: str):
        return {"token": registry.issue_token(conn, operator_id)}

    @app.post("/operators", status_code=201)
    def signup(req: OperatorSignup):
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
