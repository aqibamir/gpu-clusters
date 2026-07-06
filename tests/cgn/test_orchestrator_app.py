from fastapi.testclient import TestClient

from cgn.node.identity import NodeIdentity
from cgn.orchestrator_app import create_app, seed_operator

CAPS = {"models": ["echo-model"], "max_context": 4096, "throughput_hint": "laptop"}


def make_client():
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    return TestClient(app)


def enroll(client, ident):
    token = client.post("/operators/op1/tokens").json()["token"]
    r = client.post("/enroll", json={
        "enrollment_token": token, "node_public_key": ident.public_key_hex,
        "capabilities": CAPS, "endpoint_info": "outbound-only"})
    assert r.status_code == 200
    return r.json()["node_id"]


def hb(client, ident, node_id, load=0):
    return client.post("/heartbeat", json={
        "node_id": node_id, "signature": ident.sign(f"hb|{node_id}|{load}"),
        "current_load": load})


def test_enroll_then_canary_flips_eligibility(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    assert hb(client, ident, node_id).json()["dispatch_eligible"] is False

    # node polls: gets its canary even while pending
    job = client.post(f"/nodes/{node_id}/poll").json()
    assert "Repeat exactly:" in job["placeholdered_prompt"]
    nonce = job["placeholdered_prompt"].split(": ")[1]
    completion = f"Sure: {nonce}"
    client.post("/results", json={
        "job_id": job["job_id"], "placeholdered_completion": completion,
        "node_id": node_id, "node_signature": ident.sign(f"{job['job_id']}|{completion}"),
        "completed_at": 1.0})
    assert hb(client, ident, node_id).json()["dispatch_eligible"] is True  # TR-24


def test_failed_canary_keeps_node_ineligible(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    hb(client, ident, node_id)
    job = client.post(f"/nodes/{node_id}/poll").json()
    client.post("/results", json={
        "job_id": job["job_id"], "placeholdered_completion": "no idea",
        "node_id": node_id, "node_signature": ident.sign(f"{job['job_id']}|no idea"),
        "completed_at": 1.0})
    assert hb(client, ident, node_id).json()["dispatch_eligible"] is False


def test_job_round_trip_over_http(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    hb(client, ident, node_id)
    canary = client.post(f"/nodes/{node_id}/poll").json()
    nonce = canary["placeholdered_prompt"].split(": ")[1]
    client.post("/results", json={
        "job_id": canary["job_id"], "placeholdered_completion": nonce,
        "node_id": node_id, "node_signature": ident.sign(f"{canary['job_id']}|{nonce}"),
        "completed_at": 1.0})
    hb(client, ident, node_id)

    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": "⟦PERSON_1⟧ owes rent"}).json()["job_id"]
    job = client.post(f"/nodes/{node_id}/poll").json()
    assert job["job_id"] == jid
    completion = f"Summary: {job['placeholdered_prompt']}"
    client.post("/results", json={
        "job_id": jid, "placeholdered_completion": completion, "node_id": node_id,
        "node_signature": ident.sign(f"{jid}|{completion}"), "completed_at": 2.0})
    status = client.get(f"/jobs/{jid}").json()
    assert status["state"] == "done"
    assert status["completions"][0]["completion"] == completion
    pub = client.get(f"/nodes/{node_id}/pubkey").json()
    assert pub["public_key"] == ident.public_key_hex


def test_empty_poll_returns_204(tmp_path):
    client = make_client()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll(client, ident)
    hb(client, ident, node_id)
    canary = client.post(f"/nodes/{node_id}/poll").json()
    nonce = canary["placeholdered_prompt"].split(": ")[1]
    client.post("/results", json={
        "job_id": canary["job_id"], "placeholdered_completion": nonce,
        "node_id": node_id, "node_signature": ident.sign(f"{canary['job_id']}|{nonce}"),
        "completed_at": 1.0})
    assert client.post(f"/nodes/{node_id}/poll").status_code == 204
