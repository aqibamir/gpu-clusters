"""The loop the whole architecture exists to prove: raw PII in → routed →
anonymized → previewed → dispatched to an untrusted node → signed result →
verified → restored — with node churn, and with the orchestrator provably
PII-blind (a full dump of Zone-2 state contains no raw entity)."""

import threading

from fastapi.testclient import TestClient

from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.fake import EchoBackend
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.cgn import CGNProvider
from cmndr.router import ThresholdRouter
from cmndr.types import RequestItem
from cgn.node.worker import NodeWorker
from cgn.orchestrator_app import create_app, seed_operator

RAW_VALUES = ["Jane Smith", "Acme Corp", "jane@acme.com"]
DETECTOR = {"Jane Smith": "PERSON", "Acme Corp": "ORG", "jane@acme.com": "EMAIL_ADDRESS"}


def make_network(tmp_path, n_nodes):
    app = create_app(":memory:")
    seed_operator(app.state.conn, "op1", "op@example.com")
    client = TestClient(app)
    workers = []
    for i in range(n_nodes):
        w = NodeWorker(client, EchoBackend(), tmp_path / f"n{i}", models=["echo-model"])
        token = client.post("/operators/op1/tokens").json()["token"]
        w.enroll(token)
        w.heartbeat()
        w.poll_once()
        w.heartbeat()
        workers.append(w)
    return app, client, workers


def make_pipeline(client, tmp_path):
    return Pipeline(
        router=ThresholdRouter(),
        anonymizer=Anonymizer(WordlistDetector(DETECTOR)),
        local_backend=EchoBackend(),
        provider=CGNProvider(client, "echo-model", timeout=30),
        audit=AuditLog(tmp_path / "audit.jsonl"))


def pump(workers, stop):
    while not stop.is_set():
        for w in list(workers):
            try:
                w.poll_once()
            except Exception:
                pass


def test_full_loop_with_churn_and_pii_blind_orchestrator(tmp_path):
    app, client, workers = make_network(tmp_path, n_nodes=3)
    pipe = make_pipeline(client, tmp_path)

    stop = threading.Event()
    pumper = threading.Thread(target=pump, args=(workers, stop))
    pumper.start()
    try:
        items = [RequestItem(f"r{i}", "summarize",
                             f"Doc {i}: Jane Smith of Acme Corp (jane@acme.com) owes rent.",
                             sensitivity_hint="high")
                 for i in range(10)]
        responses = []
        for i, item in enumerate(items):
            if i == 5:
                workers.pop()      # churn: a node vanishes mid-batch (TR-14)
            responses.append(pipe.process_item(item, approve=lambda p: p.accept()))
    finally:
        stop.set()
        pumper.join(timeout=10)

    # every response restored: raw values back, no placeholders left
    assert len(responses) == 10
    for resp in responses:
        assert resp.route == "escalate"
        assert "Jane Smith" in resp.text
        assert "⟦" not in resp.text
        assert resp.restoration_anomalies == []

    # TR-15/16: dump ALL Zone-2 state; no raw entity value anywhere
    conn = app.state.conn
    dump = "\n".join("|".join(str(v) for v in row)
                     for table in ("jobs", "assignments", "nodes")
                     for row in conn.execute(f"SELECT * FROM {table}").fetchall())
    for raw in RAW_VALUES:
        assert raw not in dump

    # audit shows the escalations, placeholdered (TR-8 over the network)
    audit = AuditLog(tmp_path / "audit.jsonl").entries()
    assert sum(1 for e in audit if e["kind"] == "escalation") == 10
