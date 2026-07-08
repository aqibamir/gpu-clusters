"""Local end-to-end demo: launches the Zone-2 orchestrator, the Zone-1 customer
console, and one worker node — all in one process — so both frontends are fully
clickable in a browser and jobs run on a real local model when one is available.

    uv run python scripts/demo.py            # real Mistral-7B if present, else Echo
    uv run python scripts/demo.py --echo     # force the instant Echo backend

Then open:
    http://127.0.0.1:8100/            (customer console — submit jobs)
    http://127.0.0.1:8000/dashboard   (operator dashboard — operator id: demo)

Notes:
  * The worker runs `run_forever()`, which heartbeats every loop — without that a
    node goes stale after the heartbeat TTL and the dispatcher stops giving it work.
  * The console's local backend and the worker share ONE model instance; llama.cpp
    is not thread-safe, so access is serialized through a lock.
"""

import argparse
import threading
import tempfile
import time
from pathlib import Path

import httpx
import uvicorn

from cgn.orchestrator_app import create_app, seed_operator
from cgn.node.worker import NodeWorker
from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.base import Completion
from cmndr.backends.fake import EchoBackend
from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                     llamacpp_available)
from cmndr.detect import WordlistDetector
from cmndr.detectors.presidio import PresidioDetector, spacy_model_available
from cmndr.pipeline import Pipeline
from cmndr.providers.cgn import CGNProvider
from cmndr.router import ThresholdRouter
from cmndr.routers.heuristic import HeuristicRouter

MODEL_SPEC = "mistral-7b-instruct"


class LockedBackend:
    """Serializes access to a non-thread-safe backend (llama.cpp) so the console's
    local path and the worker can share one loaded model without racing."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._lock = threading.Lock()

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion:
        with self._lock:
            return self._inner.infer(prompt, max_tokens=max_tokens)


def _serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--orch-port", type=int, default=8000)
    p.add_argument("--console-port", type=int, default=8100)
    p.add_argument("--echo", action="store_true", help="force the Echo backend")
    args = p.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="cmndr-demo-"))

    use_real = (not args.echo) and llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()
    if use_real:
        print(f"[demo] real model: {DEFAULT_MODEL_PATH}", flush=True)
        print("[demo] loading into memory (first run compiles Metal shaders, ~1-4 min)…",
              flush=True)
        t0 = time.time()
        backend = LockedBackend(LlamaCppBackend(DEFAULT_MODEL_PATH, n_ctx=2048))
        warm = backend.infer("Reply with one word: ready.", max_tokens=8)
        print(f"[demo] model warm in {time.time() - t0:.0f}s → {warm.text.strip()!r}",
              flush=True)
    else:
        print("[demo] using Echo backend (no real model) — responses are stubs.", flush=True)
        backend = EchoBackend()

    # Zone 2 — orchestrator
    orch = create_app(str(tmp / "demo.db"))
    seed_operator(orch.state.conn, "demo", "demo@lachaine.example")
    osrv = _serve(orch, args.orch_port)

    # Zone 1 — device pipeline (real anonymization when spaCy is present)
    if spacy_model_available("en_core_web_sm"):
        detector = PresidioDetector()
        router = HeuristicRouter(detector)
    else:
        detector = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
        router = ThresholdRouter()

    orch_url = f"http://127.0.0.1:{args.orch_port}"
    pipe = Pipeline(router, Anonymizer(detector), backend,
                    CGNProvider(httpx.Client(base_url=orch_url, timeout=600),
                                MODEL_SPEC, timeout=300),
                    audit=AuditLog(str(tmp / "console_audit.jsonl")))
    from console.app import create_console_app
    csrv = _serve(create_console_app(pipe), args.console_port)

    while not (osrv.started and csrv.started):
        time.sleep(0.05)

    # one worker sharing the same (already-loaded) model — run_forever heartbeats
    wc = httpx.Client(base_url=orch_url, timeout=600)
    token = wc.post("/operators/demo/tokens").json()["token"]
    worker = NodeWorker(wc, backend, tmp / "node0", models=[MODEL_SPEC])
    worker.enroll(token)
    threading.Thread(target=worker.run_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()

    time.sleep(1.0)  # let the first heartbeat + canary land
    print("[demo] READY", flush=True)
    print(f"CONSOLE   → http://127.0.0.1:{args.console_port}/", flush=True)
    print(f"DASHBOARD → http://127.0.0.1:{args.orch_port}/dashboard  (operator id: demo)",
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[demo] shutting down.")


if __name__ == "__main__":
    main()
