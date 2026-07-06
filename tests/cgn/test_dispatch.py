from cgn.contract import Result
from cgn.db import connect
from cgn.dispatch import (check_timeouts, job_status, poll_for_work,
                          submit_job, submit_result)


def add_node(conn, node_id, models='["echo-model"]', status="eligible",
             reputation=1.0, hb=1000.0):
    conn.execute(
        "INSERT INTO nodes (node_id, operator_id, public_key, capabilities, "
        "endpoint_info, status, reputation, last_heartbeat) VALUES (?,?,?,?,?,?,?,?)",
        (node_id, "op1", "aa", f'{{"models": {models}, "max_context": 4096, '
         f'"throughput_hint": "x"}}', "o", status, reputation, hb))
    conn.commit()


def test_capable_node_gets_job_incapable_does_not():
    conn = connect(":memory:")
    add_node(conn, "n1")
    add_node(conn, "n2", models='["other-model"]')
    jid = submit_job(conn, "echo-model", "⟦PERSON_1⟧ owes rent", now=1000.0)
    assert poll_for_work(conn, "n2", now=1001.0) is None      # TR-13
    job = poll_for_work(conn, "n1", now=1001.0)
    assert job is not None and job.job_id == jid
    assert job.placeholdered_prompt == "⟦PERSON_1⟧ owes rent"
    # second poll: nothing left
    assert poll_for_work(conn, "n1", now=1002.0) is None


def test_pending_or_dead_nodes_never_polled_work():
    conn = connect(":memory:")
    add_node(conn, "pending", status="pending-verification")
    add_node(conn, "dead", hb=0.0)
    submit_job(conn, "echo-model", "p", now=1000.0)
    assert poll_for_work(conn, "pending", now=1001.0) is None
    assert poll_for_work(conn, "dead", now=1001.0) is None


def test_result_completes_job():
    conn = connect(":memory:")
    add_node(conn, "n1")
    jid = submit_job(conn, "echo-model", "p", now=1000.0)
    poll_for_work(conn, "n1", now=1001.0)
    submit_result(conn, Result(job_id=jid, placeholdered_completion="done!",
                               node_id="n1", node_signature="sig",
                               completed_at=1002.0), now=1002.0)
    st = job_status(conn, jid)
    assert st["state"] == "done"
    assert st["completions"][0]["completion"] == "done!"


def test_timeout_requeues_then_fails_after_max_attempts():
    conn = connect(":memory:")
    add_node(conn, "n1")
    jid = submit_job(conn, "echo-model", "p", timeout_s=10, now=1000.0)
    for attempt in range(3):
        assert poll_for_work(conn, "n1", now=1001.0 + attempt) is not None
        requeued = check_timeouts(conn, now=2000.0 + attempt)
        if attempt < 2:
            assert requeued == [jid]           # TR-14: reassignable
        else:
            assert requeued == []
    assert job_status(conn, jid)["state"] == "failed"


def test_redundant_job_assigned_to_two_distinct_nodes():
    conn = connect(":memory:")
    add_node(conn, "n1")
    add_node(conn, "n2")
    jid = submit_job(conn, "echo-model", "p", redundant=True, now=1000.0)
    j1 = poll_for_work(conn, "n1", now=1001.0)
    j2 = poll_for_work(conn, "n2", now=1001.0)
    assert j1.job_id == jid and j2.job_id == jid
    assert poll_for_work(conn, "n1", now=1002.0) is None  # never twice to same node
