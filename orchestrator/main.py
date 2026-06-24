"""
Orchestrator — central dispatcher for the inference network.

Usage:
    DB_PATH=orchestrator.db uvicorn orchestrator.main:app --port 8000
"""

import asyncio
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.db import get_conn
from orchestrator.registry import (
    get_node,
    online_nodes,
    record_heartbeat,
    register_node,
)
from orchestrator.scheduler import pick_node


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def verify_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    """
    Validates X-Api-Key against the API_KEY env var.
    If API_KEY is not set, validation is skipped (local dev convenience).
    """
    required = os.environ.get("API_KEY", "")
    if required and not secrets.compare_digest(x_api_key, required):
        raise HTTPException(status_code=401, detail="Invalid API key")


_Auth = Depends(verify_api_key)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class JobSubmit(BaseModel):
    model: str
    prompt: str
    max_tokens: int = 512
    priority: int = 0


class JobStatus(BaseModel):
    job_id: str
    status: str
    result: Optional[str] = None
    assigned_node: Optional[str] = None
    submitted_at: int
    completed_at: Optional[int] = None


class NodeRegistration(BaseModel):
    node_id: str
    os: str = "darwin"
    chip: str = ""
    ram_gb: int = 0
    max_model: str = ""
    throughput_class: str = "medium"
    models_loaded: list[str] = []
    base_url: str = ""


class NodeResult(BaseModel):
    job_id: str
    output: str
    tokens_generated: int
    latency_ms: float


# ---------------------------------------------------------------------------
# Background dispatch loop
# ---------------------------------------------------------------------------

_dispatch_task: Optional[asyncio.Task] = None


async def _dispatch_loop():
    """Poll for queued jobs every 2 s and dispatch to an available node."""
    while True:
        try:
            await _try_dispatch_queued()
        except Exception as e:
            print(f"[orchestrator] dispatch loop error: {e}")
        await asyncio.sleep(2)


async def _try_dispatch_queued():
    with get_conn() as conn:
        queued = conn.execute(
            "SELECT job_id, model, prompt FROM jobs WHERE status = 'queued' LIMIT 10"
        ).fetchall()

        for row in queued:
            job_id = row["job_id"]
            model = row["model"]
            node = pick_node(model, conn)
            if node is None:
                continue

            # Mark as dispatched
            conn.execute(
                "UPDATE jobs SET status = 'dispatched', assigned_node = ? WHERE job_id = ?",
                (node.node_id, job_id),
            )
            conn.commit()

            asyncio.create_task(
                _dispatch_to_node(job_id, model, row["prompt"], node)
            )


async def _dispatch_to_node(job_id: str, model: str, prompt: str, node):
    payload = {
        "job_id": job_id,
        "prompt": prompt,
        "max_tokens": 512,
        "is_challenge": False,
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{node.base_url}/run", json=payload)
            r.raise_for_status()
            data = r.json()

        with get_conn() as conn:
            conn.execute(
                """UPDATE jobs
                   SET status = 'done', result = ?, completed_at = ?
                   WHERE job_id = ?""",
                (data["output"], int(time.time()), job_id),
            )
            conn.commit()

            # Award credits: 1 credit per token, discounted by latency
            latency_ms = data.get("latency_ms", 1000)
            latency_discount = max(0.5, 1.0 - (latency_ms / 60_000))
            credit = data["tokens_generated"] * 0.001 * latency_discount
            conn.execute(
                """INSERT INTO credits
                   (node_id, job_id, tokens_generated, latency_ms, credit_amount, awarded_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (node.node_id, job_id, data["tokens_generated"],
                 latency_ms, round(credit, 6), int(time.time())),
            )
            conn.commit()

    except Exception as e:
        print(f"[orchestrator] dispatch to {node.node_id} failed for {job_id}: {e}")
        with get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed' WHERE job_id = ?",
                (job_id,),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dispatch_task
    _dispatch_task = asyncio.create_task(_dispatch_loop())
    yield
    _dispatch_task.cancel()
    try:
        await _dispatch_task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="GPU Cluster Orchestrator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND = Path(__file__).parent.parent / "frontend"
if _FRONTEND.exists():
    app.mount("/ui", StaticFiles(directory=_FRONTEND, html=True), name="frontend")


# --- Job endpoints ---

@app.post("/jobs", status_code=201, dependencies=[_Auth])
async def submit_job(job: JobSubmit):
    job_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs (job_id, status, model, prompt, submitted_at)
               VALUES (?, 'queued', ?, ?, ?)""",
            (job_id, job.model, job.prompt, int(time.time())),
        )
        conn.commit()
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", response_model=JobStatus, dependencies=[_Auth])
async def get_job(job_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(
        job_id=row["job_id"],
        status=row["status"],
        result=row["result"],
        assigned_node=row["assigned_node"],
        submitted_at=row["submitted_at"],
        completed_at=row["completed_at"],
    )


@app.get("/jobs/{job_id}/status", dependencies=[_Auth])
async def job_status(job_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, assigned_node FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": row["status"], "assigned_node": row["assigned_node"]}


# --- Node endpoints ---

@app.post("/nodes/register", status_code=201, dependencies=[_Auth])
async def register(payload: NodeRegistration):
    with get_conn() as conn:
        register_node(conn, payload.model_dump())
    return {"node_id": payload.node_id, "registered": True}


@app.post("/nodes/{node_id}/heartbeat", dependencies=[_Auth])
async def heartbeat(node_id: str):
    with get_conn() as conn:
        found = record_heartbeat(conn, node_id)
    if not found:
        raise HTTPException(status_code=404, detail="Node not registered")
    return {"accepted": True}


@app.post("/nodes/{node_id}/result", dependencies=[_Auth])
async def receive_result(node_id: str, body: NodeResult):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE job_id = ? AND assigned_node = ?",
            (body.job_id, node_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found for this node")

        conn.execute(
            """UPDATE jobs SET status = 'done', result = ?, completed_at = ?
               WHERE job_id = ?""",
            (body.output, int(time.time()), body.job_id),
        )

        latency_discount = max(0.5, 1.0 - (body.latency_ms / 60_000))
        credit = body.tokens_generated * 0.001 * latency_discount
        conn.execute(
            """INSERT INTO credits
               (node_id, job_id, tokens_generated, latency_ms, credit_amount, awarded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (node_id, body.job_id, body.tokens_generated,
             body.latency_ms, round(credit, 6), int(time.time())),
        )
        conn.commit()

    return {"credited": True, "credit_amount": round(credit, 6)}


# --- Registry info ---

@app.get("/nodes")
async def list_nodes():
    with get_conn() as conn:
        nodes = online_nodes(conn)
    return [
        {
            "node_id": n.node_id,
            "chip": n.chip,
            "ram_gb": n.ram_gb,
            "reputation": n.reputation,
            "models_loaded": n.models_loaded,
        }
        for n in nodes
    ]
