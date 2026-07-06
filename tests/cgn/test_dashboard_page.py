from fastapi.testclient import TestClient

from cgn.orchestrator_app import create_app


def test_dashboard_served():
    client = TestClient(create_app(":memory:"))
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "cgn operator dashboard" in r.text.lower()
    assert "de-identified payload" in r.text     # TR-30: the boundary, visible
