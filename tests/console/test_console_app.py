import time

from fastapi.testclient import TestClient

from cgn.contract import StatusEvent
from cmndr.anonymizer import Anonymizer
from cmndr.backends.fake import EchoBackend
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.fake import EchoProvider
from cmndr.router import ThresholdRouter
from console.app import create_console_app


def make_client():
    pipe = Pipeline(ThresholdRouter(),
                    Anonymizer(WordlistDetector({"Jane Smith": "PERSON"})),
                    EchoBackend(), EchoProvider())
    return TestClient(create_console_app(pipe))


def wait_stage(client, rid, stage, timeout=5.0):
    deadline = time.time() + timeout
    r = None
    while time.time() < deadline:
        r = client.get(f"/requests/{rid}").json()
        if r["stage"] == stage:
            return r
        time.sleep(0.02)
    raise AssertionError(f"never reached {stage}: {r}")


def test_local_request_completes_without_preview():
    client = make_client()
    rid = client.post("/requests", json={"payload": "what is 2+2",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "low"}).json()["request_id"]
    r = wait_stage(client, rid, "done")
    assert r["result"]["route"] == "local"


def test_escalation_waits_at_preview_and_result_restores():
    client = make_client()
    rid = client.post("/requests", json={"payload": "Jane Smith owes rent",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "high"}).json()["request_id"]
    r = wait_stage(client, rid, "preview")
    assert r["preview"]["text"] == "⟦PERSON_1⟧ owes rent"      # TR-31: parked
    assert r["result"] is None

    client.post(f"/requests/{rid}/approve", json={"accept": True,
                                                  "remove": [], "add": []})
    r = wait_stage(client, rid, "done")
    assert "Jane Smith" in r["result"]["text"]                 # restored on-device


def test_rejection_fails_the_request():
    client = make_client()
    rid = client.post("/requests", json={"payload": "Jane Smith owes rent",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "high"}).json()["request_id"]
    wait_stage(client, rid, "preview")
    client.post(f"/requests/{rid}/approve", json={"accept": False,
                                                  "remove": [], "add": []})
    r = wait_stage(client, rid, "failed")
    assert r["result"] is None


def test_lifecycle_events_are_status_events_in_order():
    client = make_client()
    rid = client.post("/requests", json={"payload": "Jane Smith owes rent",
                                         "task_type": "summarize",
                                         "sensitivity_hint": "high"}).json()["request_id"]
    wait_stage(client, rid, "preview")
    client.post(f"/requests/{rid}/approve", json={"accept": True,
                                                  "remove": [], "add": []})
    wait_stage(client, rid, "done")
    events = client.get("/events").json()["events"]
    mine = [e for e in events if e["job_id"] == rid]
    for ev in mine:
        StatusEvent.model_validate(ev)                          # TR-28 on device side
        assert "Jane Smith" not in str(ev)
    states = [e["state"] for e in mine]
    assert states == ["routing", "anonymizing", "preview",
                      "dispatched", "running", "restoring", "done"]   # TR-27
