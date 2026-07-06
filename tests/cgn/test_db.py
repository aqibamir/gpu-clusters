from cgn.db import connect


def test_schema_tables_exist():
    conn = connect(":memory:")
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"operators", "enrollment_tokens", "nodes", "jobs", "assignments"} <= names


def test_defaults():
    conn = connect(":memory:")
    conn.execute("INSERT INTO nodes (node_id, operator_id, public_key, capabilities, endpoint_info) "
                 "VALUES ('n1','op1','ab','{}','out')")
    row = conn.execute("SELECT * FROM nodes").fetchone()
    assert row["status"] == "pending-verification"
    assert row["reputation"] == 1.0
    conn.execute("INSERT INTO jobs (job_id, model_spec, prompt, params, created_at) "
                 "VALUES ('j1','m','p','{}',0)")
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["state"] == "queued"
    assert job["kind"] == "inference"
    assert job["attempts"] == 0
