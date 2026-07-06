"""§7.4: the node agent. Sees only placeholdered text — never the map, raw
payload, or customer identity. Dial-out only (CD1). 'Register a GPU' ==
'run this module with a token'."""

import argparse
import time
from pathlib import Path

import httpx

from cgn.contract import Capabilities, EnrollRequest, HeartbeatRequest, Job, Result
from cgn.node.identity import NodeIdentity
from cmndr.backends.base import Backend


class NodeWorker:
    def __init__(self, client: httpx.Client, backend: Backend, key_dir: Path,
                 models: list[str], throughput_hint: str = "laptop") -> None:
        self._client = client
        self._backend = backend
        key_dir = Path(key_dir)
        if (key_dir / "node_key.pem").exists():
            self._ident = NodeIdentity.load(key_dir)
        else:
            self._ident = NodeIdentity.create(key_dir)
        self._models = models
        self._throughput_hint = throughput_hint
        self.node_id: str | None = None
        self._load = 0

    def enroll(self, token: str) -> str:
        req = EnrollRequest(
            enrollment_token=token,
            node_public_key=self._ident.public_key_hex,   # ONLY the public key
            capabilities=Capabilities(models=self._models, max_context=4096,
                                      throughput_hint=self._throughput_hint),
            endpoint_info="outbound-only")
        r = self._client.post("/enroll", json=req.model_dump())
        r.raise_for_status()
        self.node_id = r.json()["node_id"]
        return self.node_id

    def heartbeat(self) -> bool:
        req = HeartbeatRequest(node_id=self.node_id,
                               signature=self._ident.sign(f"hb|{self.node_id}|{self._load}"),
                               current_load=self._load)
        r = self._client.post("/heartbeat", json=req.model_dump())
        r.raise_for_status()
        return bool(r.json()["dispatch_eligible"])

    def poll_once(self) -> str | None:
        r = self._client.post(f"/nodes/{self.node_id}/poll")
        if r.status_code == 204:
            return None
        job = Job.model_validate(r.json())
        self._load = 1
        try:
            completion = self._backend.infer(
                job.placeholdered_prompt,
                max_tokens=job.params.get("max_tokens", 512)).text
        finally:
            self._load = 0
        result = Result(job_id=job.job_id, placeholdered_completion=completion,
                        node_id=self.node_id,
                        node_signature=self._ident.sign(f"{job.job_id}|{completion}"),
                        completed_at=time.time())
        self._client.post("/results", json=result.model_dump()).raise_for_status()
        return job.job_id

    def run_forever(self, poll_interval: float = 2.0) -> None:
        while True:
            self.heartbeat()
            if self.poll_once() is None:
                time.sleep(poll_interval)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--orchestrator", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--models", nargs="+", default=["mistral-7b-instruct-v0.2.Q4_K_M"])
    p.add_argument("--key-dir", default="~/.cmndr-node")
    args = p.parse_args()

    from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                         llamacpp_available)
    from cmndr.backends.fake import EchoBackend
    backend = (LlamaCppBackend(DEFAULT_MODEL_PATH)
               if llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()
               else EchoBackend())
    client = httpx.Client(base_url=args.orchestrator, timeout=300)
    worker = NodeWorker(client, backend, Path(args.key_dir).expanduser(), args.models)
    worker.enroll(args.token)
    worker.run_forever()


if __name__ == "__main__":
    main()
