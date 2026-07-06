from cmndr.backends.fake import EchoBackend
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app, seed_operator
from fastapi.testclient import TestClient


def make():
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    client = TestClient(app)
    return app, client


def test_worker_enrolls_passes_canary_and_serves_job(tmp_path):
    app, client = make()
    worker = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    node_id = worker.enroll(token)
    assert node_id.startswith("node-")
    assert worker.heartbeat() is False       # pending until canary

    handled = worker.poll_once()             # canary: EchoBackend echoes the nonce
    assert handled is not None
    assert worker.heartbeat() is True        # TR-24 passed

    jid = client.post("/jobs", json={"model_spec": "echo-model",
                                     "placeholdered_prompt": "⟦PERSON_1⟧ hi"}).json()["job_id"]
    assert worker.poll_once() == jid
    status = client.get(f"/jobs/{jid}").json()
    assert status["state"] == "done"
    assert "⟦PERSON_1⟧" in status["completions"][0]["completion"]


def test_enrollment_traffic_contains_only_public_key(tmp_path, monkeypatch):
    app, client = make()
    sent = []
    original_post = client.post

    def spy_post(url, **kw):
        sent.append((url, kw.get("json")))
        return original_post(url, **kw)

    monkeypatch.setattr(client, "post", spy_post)
    worker = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    worker.enroll(token)
    private_pem = (tmp_path / "node_key.pem").read_text()
    for _, body in sent:
        assert body is None or "PRIVATE" not in str(body)          # TR-23
        assert body is None or private_pem not in str(body)


def test_poll_once_returns_none_when_no_work(tmp_path):
    app, client = make()
    worker = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    worker.enroll(token)
    worker.heartbeat()
    worker.poll_once()                        # canary
    assert worker.poll_once() is None
