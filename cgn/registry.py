"""§7.2/§8: node registry. Registration is open but registration ≠ trust —
trust arrives via canary (Task 6) and reputation (Task 8). Tokens are
single-use, time-limited, account-bound (TR-22); consent is checked (TR-21)."""

import secrets
import time
import uuid
import json

from cgn.contract import EnrollRequest, EnrollResponse, HeartbeatRequest, HeartbeatResponse
from cgn.node.identity import verify

HEARTBEAT_TTL_S = 90


class EnrollmentError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def issue_token(conn, operator_id: str, ttl_s: float = 900,
                now: float | None = None) -> str:
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO enrollment_tokens VALUES (?,?,?,?,0)",
                 (token, operator_id, now, now + ttl_s))
    conn.commit()
    return token


def enroll(conn, req: EnrollRequest, now: float | None = None) -> EnrollResponse:
    now = time.time() if now is None else now
    row = conn.execute("SELECT * FROM enrollment_tokens WHERE token=?",
                       (req.enrollment_token,)).fetchone()
    if row is None:
        raise EnrollmentError("invalid")
    if row["spent"]:
        raise EnrollmentError("spent")
    if now > row["expires_at"]:
        raise EnrollmentError("expired")
    op = conn.execute(
        "SELECT * FROM operators WHERE operator_id=? AND consent_at IS NOT NULL",
        (row["operator_id"],)).fetchone()
    if op is None:
        raise EnrollmentError("no-consent")

    node_id = f"node-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO nodes (node_id, operator_id, public_key, capabilities, endpoint_info) "
        "VALUES (?,?,?,?,?)",
        (node_id, row["operator_id"], req.node_public_key,
         json.dumps(req.capabilities.model_dump()), req.endpoint_info))
    conn.execute("UPDATE enrollment_tokens SET spent=1 WHERE token=?",
                 (req.enrollment_token,))
    conn.commit()
    return EnrollResponse(node_id=node_id, status="pending-verification")


def heartbeat(conn, req: HeartbeatRequest, now: float | None = None) -> HeartbeatResponse:
    now = time.time() if now is None else now
    row = conn.execute("SELECT * FROM nodes WHERE node_id=?", (req.node_id,)).fetchone()
    if row is None:
        return HeartbeatResponse(ack=False, dispatch_eligible=False)
    if not verify(row["public_key"], f"hb|{req.node_id}|{req.current_load}", req.signature):
        return HeartbeatResponse(ack=False, dispatch_eligible=False)
    conn.execute("UPDATE nodes SET last_heartbeat=?, current_load=? WHERE node_id=?",
                 (now, req.current_load, req.node_id))
    conn.commit()
    return HeartbeatResponse(ack=True, dispatch_eligible=row["status"] == "eligible")


def alive_nodes(conn, now: float | None = None, ttl_s: float = HEARTBEAT_TTL_S):
    now = time.time() if now is None else now
    return conn.execute("SELECT * FROM nodes WHERE last_heartbeat >= ?",
                        (now - ttl_s,)).fetchall()
