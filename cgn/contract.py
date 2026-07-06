"""§12.0 — the frozen shared wire contract. Changing this file is the only
thing that requires both tracks to sync. Every model forbids unknown fields;
for StatusEvent that IS the TR-28 metadata-only guarantee."""

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capabilities(_Frozen):
    models: list[str]
    max_context: int
    throughput_hint: str


class EnrollRequest(_Frozen):
    enrollment_token: str
    node_public_key: str  # hex; ONLY the public key ever leaves the node (TR-23)
    capabilities: Capabilities
    endpoint_info: str


class EnrollResponse(_Frozen):
    node_id: str
    status: str  # "pending-verification"


class HeartbeatRequest(_Frozen):
    node_id: str
    signature: str  # over "hb|{node_id}|{current_load}"
    current_load: int


class HeartbeatResponse(_Frozen):
    ack: bool
    dispatch_eligible: bool


class Job(_Frozen):
    job_id: str
    model_spec: str
    placeholdered_prompt: str  # the ONLY content field; already placeholdered
    params: dict
    issued_at: float
    timeout: float
    job_signature: str  # sha256 checksum of (job_id|model_spec|prompt)


class Result(_Frozen):
    job_id: str
    placeholdered_completion: str
    node_id: str
    node_signature: str  # Ed25519 over "{job_id}|{completion}", hex
    completed_at: float


class StatusEvent(_Frozen):
    job_id: str
    state: str      # queued|assigned|running|done|failed|reassigned|divergent
    pct: float
    node_id: str | None
    elapsed_ms: int
    stage: str
