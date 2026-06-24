"""
Node agent — runs on each contributor Mac.
Loads a quantized model at startup and serves inference jobs over HTTP.

Usage:
    MODEL_PATH=/path/to/model.gguf uvicorn node_agent.main:app --port 8001
"""

import os
import time
import platform
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import psutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# llama-cpp-python is only required when actually running inference.
# Import is deferred so the orchestrator (which doesn't need it) can import
# this module's Pydantic models without the Metal wheel installed.
try:
    from llama_cpp import Llama
    _llama_available = True
except ImportError:
    _llama_available = False


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class JobRequest(BaseModel):
    job_id: str
    prompt: str
    max_tokens: int = 512
    is_challenge: bool = False


class JobResult(BaseModel):
    job_id: str
    output: str
    tokens_generated: int
    latency_ms: float


class HealthInfo(BaseModel):
    node_id: str
    os: str
    chip: str
    ram_gb: int
    model_loaded: Optional[str]
    status: str


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_model: Optional["Llama"] = None
_node_id: str = ""
_model_path: str = ""
_orchestrator_url: str = ""
_api_key: str = ""


def _chip_name() -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=2
        )
        raw = result.stdout.strip()
        if raw:
            return raw.lower().replace(" ", "_")
    except Exception:
        pass
    return platform.processor() or "unknown"


def _ram_gb() -> int:
    return round(psutil.virtual_memory().total / (1024 ** 3))


# ---------------------------------------------------------------------------
# Lifespan — model is loaded once at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _node_id, _model_path, _orchestrator_url

    _model_path = os.environ.get("MODEL_PATH", "")
    _orchestrator_url = os.environ.get("ORCHESTRATOR_URL", "")
    _node_id = os.environ.get("NODE_ID", platform.node())
    _api_key = os.environ.get("API_KEY", "")

    if _model_path:
        if not _llama_available:
            raise RuntimeError(
                "MODEL_PATH is set but llama-cpp-python is not installed. "
                "Install with: uv pip install 'llama-cpp-python[metal]'"
            )
        print(f"[node_agent] Loading model from {_model_path} …")
        _model = Llama(
            model_path=_model_path,
            n_ctx=4096,
            n_gpu_layers=-1,   # offload all layers to Metal
            verbose=False,
        )
        print("[node_agent] Model loaded.")
    else:
        print("[node_agent] No MODEL_PATH set — running in stub mode (health/register only).")

    # Register with orchestrator if configured
    if _orchestrator_url and _model_path:
        await _register()

    yield

    # Cleanup: nothing to do for llama.cpp (GC handles it)


async def _register():
    model_name = os.path.basename(_model_path)
    payload = {
        "node_id": _node_id,
        "os": "darwin",
        "chip": _chip_name(),
        "ram_gb": _ram_gb(),
        "max_model": model_name,
        "throughput_class": "medium",
        "models_loaded": [model_name],
        "base_url": os.environ.get("NODE_BASE_URL", ""),
    }
    headers = {"X-Api-Key": _api_key} if _api_key else {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                f"{_orchestrator_url}/nodes/register", json=payload, headers=headers
            )
            r.raise_for_status()
            print(f"[node_agent] Registered with orchestrator: {r.json()}")
    except Exception as e:
        print(f"[node_agent] Registration failed (will retry on next heartbeat): {e}")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="GPU Cluster Node Agent", lifespan=lifespan)


@app.post("/run", response_model=JobResult)
async def run_job(job: JobRequest) -> JobResult:
    if _model is None:
        raise HTTPException(status_code=503, detail="No model loaded. Set MODEL_PATH.")

    t0 = time.perf_counter()
    completion = _model(
        job.prompt,
        max_tokens=job.max_tokens,
        echo=False,
        stop=["</s>", "[INST]"],
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    output_text: str = completion["choices"][0]["text"].strip()
    tokens_generated: int = completion["usage"]["completion_tokens"]

    return JobResult(
        job_id=job.job_id,
        output=output_text,
        tokens_generated=tokens_generated,
        latency_ms=round(latency_ms, 1),
    )


@app.get("/health", response_model=HealthInfo)
async def health() -> HealthInfo:
    return HealthInfo(
        node_id=_node_id,
        os="darwin",
        chip=_chip_name(),
        ram_gb=_ram_gb(),
        model_loaded=os.path.basename(_model_path) if _model_path else None,
        status="ready" if _model is not None else "no_model",
    )


@app.post("/nodes/{node_id}/heartbeat")
async def heartbeat(node_id: str):
    """Self-heartbeat endpoint used when the orchestrator polls this node."""
    return {"node_id": node_id, "accepted": True}
