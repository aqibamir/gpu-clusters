from fastapi.testclient import TestClient

from cgn.contract import StatusEvent
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app
from cmndr.backends.fake import EchoBackend

SECRET = "⟦PERSON_1⟧ owes rent"


def setup(tmp_path):
    app = create_app(":memory:")
    client = TestClient(app)
    client.post("/operators", json={"operator_id": "op1",
                                    "email": "e@x.com", "consent": True})
    w = NodeWorker(client, EchoBackend(), tmp_path, models=["echo-model"])
    token = client.post("/operators/op1/tokens").json()["token"]
    w.enroll(token)
    w.heartbeat(); w.poll_once(); w.heartbeat()
    return app, client, w


def test_feed_is_metadata_only_and_schema_clean(tmp_path):
    app, client, w = setup(tmp_path)
    client.post("/jobs", json={"model_spec": "echo-model",
                               "placeholdered_prompt": SECRET})
    w.poll_once()
    events = client.get("/operators/op1/feed").json()["events"]
    assert len(events) == 2                     # canary + job
    for ev in events:
        StatusEvent.model_validate(ev)          # TR-28: parses under extra=forbid
        assert SECRET not in str(ev)            # no content, even placeholdered
    done = [e for e in events if e["state"] == "done"]
    assert len(done) == 2
    assert all(e["stage"] in ("inference", "canary") for e in events)


def test_feed_shows_only_that_operators_nodes(tmp_path):
    app, client, w = setup(tmp_path)
    client.post("/operators", json={"operator_id": "op2",
                                    "email": "b@x.com", "consent": True})
    assert client.get("/operators/op2/feed").json()["events"] == []
