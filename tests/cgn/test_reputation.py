from cgn.contract import Result
from cgn.db import connect
from cgn.dispatch import check_timeouts, job_status, poll_for_work, submit_job, submit_result
from cgn.reputation import FLOOR, adjust
from tests.cgn.test_dispatch import add_node


def rep(conn, node_id):
    return conn.execute("SELECT reputation, status FROM nodes WHERE node_id=?",
                        (node_id,)).fetchone()


def run_redundant(conn, completions: dict[str, str]):
    jid = submit_job(conn, "echo-model", "p", redundant=True, now=1000.0)
    for node_id in completions:
        poll_for_work(conn, node_id, now=1001.0)
    for node_id, completion in completions.items():
        submit_result(conn, Result(job_id=jid, placeholdered_completion=completion,
                                   node_id=node_id, node_signature="s",
                                   completed_at=1002.0), now=1002.0)
    return jid


def test_agreement_rewards_both():
    conn = connect(":memory:")
    add_node(conn, "n1"); add_node(conn, "n2")
    jid = run_redundant(conn, {"n1": "same answer", "n2": "same  answer"})
    assert job_status(conn, jid)["state"] == "done"
    assert rep(conn, "n1")["reputation"] == 1.0   # already at cap, agreement keeps it


def test_divergence_flags_job_and_penalizes_both():
    conn = connect(":memory:")
    add_node(conn, "n1"); add_node(conn, "n2")
    jid = run_redundant(conn, {"n1": "the real answer", "n2": "garbage to farm credit"})
    assert job_status(conn, jid)["state"] == "divergent"          # TR-18
    assert rep(conn, "n1")["reputation"] == 0.8
    assert rep(conn, "n2")["reputation"] == 0.8


def test_node_below_floor_is_ejected_and_stops_receiving_work():
    conn = connect(":memory:")
    add_node(conn, "n1", reputation=0.4)
    adjust(conn, "n1", -0.2)                                       # 0.2 < FLOOR
    assert rep(conn, "n1")["status"] == "ejected"                  # TR-19
    submit_job(conn, "echo-model", "p", now=1000.0)
    assert poll_for_work(conn, "n1", now=1001.0) is None


def test_timeout_penalizes_the_silent_node():
    conn = connect(":memory:")
    add_node(conn, "n1")
    submit_job(conn, "echo-model", "p", timeout_s=10, now=1000.0)
    poll_for_work(conn, "n1", now=1001.0)
    check_timeouts(conn, now=2000.0)
    assert rep(conn, "n1")["reputation"] == 0.8
