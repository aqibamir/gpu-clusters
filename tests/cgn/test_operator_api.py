from fastapi.testclient import TestClient

from cgn.node.identity import NodeIdentity
from cgn.node.worker import NodeWorker
from cmndr.backends.fake import EchoBackend
from cgn.orchestrator_app import create_app


def test_signup_requires_consent():
    client = TestClient(create_app(":memory:"))
    r = client.post("/operators", json={"operator_id": "op1",
                                        "email": "op@example.com", "consent": False})
    assert r.status_code == 400
    r = client.post("/operators", json={"operator_id": "op1",
                                        "email": "op@example.com", "consent": True})
    assert r.status_code == 201


def test_enrollment_under_unconsented_account_rejected(tmp_path):
    app = create_app(":memory:")
    client = TestClient(app)
    # token for an operator that never consented (row absent entirely)
    app.state.conn.execute("INSERT INTO enrollment_tokens VALUES ('t','ghost',0,9e9,0)")
    app.state.conn.commit()
    ident = NodeIdentity.create(tmp_path)
    r = client.post("/enroll", json={
        "enrollment_token": "t", "node_public_key": ident.public_key_hex,
        "capabilities": {"models": ["m"], "max_context": 1, "throughput_hint": "x"},
        "endpoint_info": "o"})
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "no-consent"


def test_operator_sees_own_nodes_with_stats(tmp_path):
    app = create_app(":memory:")
    client = TestClient(app)
    client.post("/operators", json={"operator_id": "op1",
                                    "email": "op@example.com", "consent": True})
    w = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    node_id = w.enroll(token)
    w.heartbeat(); w.poll_once(); w.heartbeat()   # canary → eligible

    nodes = client.get("/operators/op1/nodes").json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == node_id
    assert nodes[0]["status"] == "eligible"
    assert nodes[0]["jobs_done"] == 1             # the canary
    assert "reputation" in nodes[0] and "last_heartbeat" in nodes[0]
