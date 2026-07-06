"""A1/A2: the on-device dispatcher behind the Provider seam. Submits only
placeholdered payloads (the Pipeline guarantees this structurally, TR-7) and
verifies the node's signature BEFORE anything is restored (TR-17)."""

import time

import httpx

from cgn.node.identity import verify
from cmndr.types import PlaceholderedPayload


class ResultSignatureInvalid(Exception):
    pass


class JobFailed(Exception):
    pass


class CGNProvider:
    def __init__(self, client: httpx.Client, model_spec: str,
                 poll_interval: float = 0.05, timeout: float = 300.0,
                 redundant: bool = False) -> None:
        self._client = client
        self._model_spec = model_spec
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._redundant = redundant

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str:
        r = self._client.post("/jobs", json={
            "model_spec": self._model_spec,
            "placeholdered_prompt": payload.text,
            "params": {"max_tokens": max_tokens},
            "redundant": self._redundant})
        r.raise_for_status()
        return self._await_verified(r.json()["job_id"])

    def _await_verified(self, job_id: str) -> str:
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            status = self._client.get(f"/jobs/{job_id}").json()
            state = status["state"]
            if state == "done":
                comp = status["completions"][0]
                pub = self._client.get(f"/nodes/{comp['node_id']}/pubkey").json()["public_key"]
                if not verify(pub, f"{job_id}|{comp['completion']}", comp["signature"]):
                    raise ResultSignatureInvalid(job_id)
                return comp["completion"]
            if state in ("failed", "divergent"):
                raise JobFailed(state)
            time.sleep(self._poll_interval)
        raise TimeoutError(job_id)
