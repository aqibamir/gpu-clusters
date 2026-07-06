import pytest

from cgn.contract import Capabilities, EnrollRequest, HeartbeatRequest
from cgn.db import connect
from cgn.node.identity import NodeIdentity
from cgn.registry import (EnrollmentError, alive_nodes, enroll, heartbeat,
                          issue_token)

CAPS = Capabilities(models=["echo-model"], max_context=4096, throughput_hint="laptop")


def setup_conn():
    conn = connect(":memory:")
    conn.execute("INSERT INTO operators (operator_id, email, consent_at) "
                 "VALUES ('op1', 'op@example.com', 100.0)")
    conn.commit()
    return conn


def enroll_node(conn, ident, now=1000.0):
    token = issue_token(conn, "op1", now=now)
    return enroll(conn, EnrollRequest(
        enrollment_token=token, node_public_key=ident.public_key_hex,
        capabilities=CAPS, endpoint_info="outbound-only"), now=now)


def test_valid_token_enrolls_pending(tmp_path):
    conn = setup_conn()
    resp = enroll_node(conn, NodeIdentity.create(tmp_path))
    assert resp.status == "pending-verification"
    row = conn.execute("SELECT * FROM nodes").fetchone()
    assert row["operator_id"] == "op1"


def test_token_is_single_use(tmp_path):
    conn = setup_conn()
    ident = NodeIdentity.create(tmp_path)
    token = issue_token(conn, "op1", now=1000.0)
    req = EnrollRequest(enrollment_token=token, node_public_key=ident.public_key_hex,
                        capabilities=CAPS, endpoint_info="o")
    enroll(conn, req, now=1000.0)
    with pytest.raises(EnrollmentError) as e:
        enroll(conn, req, now=1001.0)
    assert e.value.reason == "spent"


def test_expired_token_rejected(tmp_path):
    conn = setup_conn()
    token = issue_token(conn, "op1", ttl_s=10, now=1000.0)
    ident = NodeIdentity.create(tmp_path)
    with pytest.raises(EnrollmentError) as e:
        enroll(conn, EnrollRequest(enrollment_token=token,
                                   node_public_key=ident.public_key_hex,
                                   capabilities=CAPS, endpoint_info="o"), now=1011.0)
    assert e.value.reason == "expired"


def test_enroll_without_consented_operator_rejected(tmp_path):
    conn = connect(":memory:")  # no operator row at all
    conn.execute("INSERT INTO enrollment_tokens VALUES ('t','ghost',1000,2000,0)")
    ident = NodeIdentity.create(tmp_path)
    with pytest.raises(EnrollmentError) as e:
        enroll(conn, EnrollRequest(enrollment_token="t",
                                   node_public_key=ident.public_key_hex,
                                   capabilities=CAPS, endpoint_info="o"), now=1500.0)
    assert e.value.reason == "no-consent"


def test_heartbeat_updates_liveness_and_bad_signature_rejected(tmp_path):
    conn = setup_conn()
    ident = NodeIdentity.create(tmp_path)
    node_id = enroll_node(conn, ident).node_id
    ok = heartbeat(conn, HeartbeatRequest(
        node_id=node_id, signature=ident.sign(f"hb|{node_id}|0"), current_load=0),
        now=2000.0)
    assert ok.ack is True
    bad = heartbeat(conn, HeartbeatRequest(
        node_id=node_id, signature="00" * 64, current_load=0), now=2001.0)
    assert bad.ack is False
    assert [r["node_id"] for r in alive_nodes(conn, now=2050.0)] == [node_id]
    assert alive_nodes(conn, now=5000.0) == []
