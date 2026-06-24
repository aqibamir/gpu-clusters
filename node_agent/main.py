"""
Node agent — runs on a contributor cloud NVIDIA GPU VM.

Loads a model via vLLM at startup and serves inference jobs over HTTP.
The orchestrator dispatches jobs to POST /run; results are returned synchronously.

Usage (single GPU):
    MODEL_NAME=mistralai/Mistral-7B-Instruct-v0.2 \
    ORCHESTRATOR_URL=https://your-orchestrator.up.railway.app \
    API_KEY=secret \
    NODE_BASE_URL=http://$(curl -s ifconfig.me):8001 \
    uvicorn node_agent.main:app --host 0.0.0.0 --port 8001

Usage (multi-GPU tensor parallel, e.g. 4×A100 for 70B):
    ray start --head --port=6379   # on primary VM
    MODEL_NAME=meta-llama/Meta-Llama-3-70B-Instruct \
    TENSOR_PARALLEL_SIZE=4 \
    uvicorn node_agent.main:app --host 0.0.0.0 --port 8001

For Mac local testing (no GPU), set MODEL_NAME=facebook/opt-125m — it runs on CPU.
"""

import os
import platform
import time
import uuid as _uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import psutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

try:
    from vllm import AsyncEngineArgs, AsyncLLMEngine, SamplingParams
    _vllm_available = True
except ImportError:
    _vllm_available = False


# ---------------------------------------------------------------------------
# Pydantic models (unchanged — same contract with orchestrator)
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
    gpu: str
    ram_gb: int
    model_loaded: Optional[str]
    tensor_parallel_size: int
    status: str


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_engine: Optional["AsyncLLMEngine"] = None
_node_id: str = ""
_model_name: str = ""
_orchestrator_url: str = ""
_api_key: str = ""
_tensor_parallel: int = 1


def _gpu_name() -> str:
    gpu = os.environ.get("GPU_NAME", "")
    if gpu:
        return gpu
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
        name = r.stdout.strip().splitlines()[0]
        if name:
            return name
    except Exception:
        pass
    return "cpu"


def _ram_gb() -> int:
    return round(psutil.virtual_memory().total / (1024 ** 3))


# ---------------------------------------------------------------------------
# Lifespan — engine loaded once at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _node_id, _model_name, _orchestrator_url, _api_key, _tensor_parallel

    _model_name      = os.environ.get("MODEL_NAME", "mistralai/Mistral-7B-Instruct-v0.2")
    _orchestrator_url = os.environ.get("ORCHESTRATOR_URL", "")
    _node_id         = os.environ.get("NODE_ID", platform.node())
    _api_key         = os.environ.get("API_KEY", "")
    _tensor_parallel  = int(os.environ.get("TENSOR_PARALLEL_SIZE", "1"))

    if not _vllm_available:
        print("[node_agent] vLLM not installed — running in stub mode.")
        print("[node_agent] Install with: pip install vllm")
    else:
        print(f"[node_agent] Loading {_model_name} "
              f"(tensor_parallel={_tensor_parallel}) …")
        args = AsyncEngineArgs(
            model=_model_name,
            tensor_parallel_size=_tensor_parallel,
            gpu_memory_utilization=float(os.environ.get("GPU_MEMORY_UTIL", "0.90")),
            dtype="auto",
            trust_remote_code=True,
        )
        _engine = AsyncLLMEngine.from_engine_args(args)
        print("[node_agent] Engine ready.")

    if _orchestrator_url and _engine is not None:
        await _register()

    yield

    # vLLM engine cleanup is handled by the GC / Ray shutdown
    if _engine is not None:
        await _engine.abort_request("shutdown")


async def _register():
    payload = {
        "node_id": _node_id,
        "os": platform.system().lower(),
        "chip": _gpu_name(),
        "ram_gb": _ram_gb(),
        "max_model": _model_name,
        "throughput_class": "high",
        "models_loaded": [_model_name],
        "base_url": os.environ.get("NODE_BASE_URL", ""),
    }
    headers = {"X-Api-Key": _api_key} if _api_key else {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                f"{_orchestrator_url}/nodes/register",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            print(f"[node_agent] Registered: {r.json()}")
    except Exception as e:
        print(f"[node_agent] Registration failed: {e}")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="GPU Cluster Node Agent (vLLM)", lifespan=lifespan)


@app.post("/run", response_model=JobResult)
async def run_job(job: JobRequest) -> JobResult:
    if _engine is None:
        raise HTTPException(
            status_code=503,
            detail="No model loaded. Set MODEL_NAME and ensure vLLM is installed.",
        )

    params = SamplingParams(
        max_tokens=job.max_tokens,
        stop=["</s>", "[INST]", "[/INST]"],
    )
    request_id = str(_uuid.uuid4())
    t0 = time.perf_counter()

    output_text = ""
    tokens_generated = 0
    async for output in _engine.generate(job.prompt, params, request_id):
        if output.finished:
            output_text = output.outputs[0].text.strip()
            tokens_generated = len(output.outputs[0].token_ids)

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    return JobResult(
        job_id=job.job_id,
        output=output_text,
        tokens_generated=tokens_generated,
        latency_ms=latency_ms,
    )


@app.get("/health", response_model=HealthInfo)
async def health() -> HealthInfo:
    return HealthInfo(
        node_id=_node_id,
        gpu=_gpu_name(),
        ram_gb=_ram_gb(),
        model_loaded=_model_name if _engine is not None else None,
        tensor_parallel_size=_tensor_parallel,
        status="ready" if _engine is not None else "no_model",
    )


@app.post("/nodes/{node_id}/heartbeat")
async def heartbeat(node_id: str):
    return {"node_id": node_id, "accepted": True}
