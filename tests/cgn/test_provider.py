import threading

import pytest
from fastapi.testclient import TestClient

from cmndr.backends.fake import EchoBackend
from cmndr.providers.cgn import CGNProvider, JobFailed, ResultSignatureInvalid
from cmndr.types import PlaceholderedPayload
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app, seed_operator


def make_network(tmp_path, n_nodes=1):
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    client = TestClient(app)
    workers = []
    for i in range(n_nodes):
        w = NodeWorker(client, EchoBackend(), tmp_path / f"n{i}", models=["echo-model"])
        token = client.post("/operators/op1/tokens").json()["token"]
        w.enroll(token)
        w.heartbeat()
        w.poll_once()   # canary
        w.heartbeat()
        workers.append(w)
    return app, client, workers


def infer_with_worker_pump(provider, payload, workers, client):
    """Drive the loop deterministically: submit, let workers pump, poll."""
    done = {}

    def run():
        done["text"] = provider.infer(payload)

    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        for w in workers:
            w.poll_once()
        t.join(timeout=0.02)
        if not t.is_alive():
            break
    t.join(timeout=5)
    assert not t.is_alive(), "provider.infer never returned"
    return done["text"]


def test_provider_round_trip_with_signature_verification(tmp_path):
    app, client, workers = make_network(tmp_path)
    provider = CGNProvider(client, "echo-model")
    payload = PlaceholderedPayload("r1", "⟦PERSON_1⟧ owes ⟦ORG_1⟧ rent", {"PERSON": 1, "ORG": 1})
    text = infer_with_worker_pump(provider, payload, workers, client)
    assert "⟦PERSON_1⟧" in text     # completion still placeholdered


def test_tampered_result_is_rejected(tmp_path):
    app, client, workers = make_network(tmp_path)
    provider = CGNProvider(client, "echo-model", timeout=5)
    payload = PlaceholderedPayload("r2", "⟦PERSON_1⟧ hi", {"PERSON": 1})

    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": payload.text}).json()["job_id"]
    workers[0].poll_once()
    # tamper with the stored completion after signing
    app.state.conn.execute(
        "UPDATE assignments SET completion='FORGED' WHERE job_id=?", (jid,))
    app.state.conn.commit()
    with pytest.raises(ResultSignatureInvalid):
        provider._await_verified(jid)          # TR-17: reject before restore


def test_failed_job_raises(tmp_path):
    app, client, workers = make_network(tmp_path)
    provider = CGNProvider(client, "echo-model", timeout=5)
    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": "p"}).json()["job_id"]
    app.state.conn.execute("UPDATE jobs SET state='failed' WHERE job_id=?", (jid,))
    app.state.conn.commit()
    with pytest.raises(JobFailed):
        provider._await_verified(jid)
