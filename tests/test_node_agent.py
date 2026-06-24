"""
Unit tests for the node agent — no model required (stub mode).
vLLM is not installed locally; the agent starts with _engine=None.
"""

import pytest
from fastapi.testclient import TestClient

import os
os.environ.setdefault("MODEL_NAME", "")
os.environ.setdefault("ORCHESTRATOR_URL", "")
os.environ.setdefault("NODE_ID", "test-node")

from node_agent.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_no_model(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "no_model"
    assert data["node_id"] == "test-node"


def test_run_without_model_returns_503(client):
    r = client.post("/run", json={
        "job_id": "test-job-1",
        "prompt": "Hello",
        "max_tokens": 10,
    })
    assert r.status_code == 503


def test_heartbeat_endpoint(client):
    r = client.post("/nodes/test-node/heartbeat")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
