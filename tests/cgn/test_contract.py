import pytest
from pydantic import ValidationError

from cgn.contract import (Capabilities, EnrollRequest, HeartbeatRequest, Job,
                          Result, StatusEvent)


def test_status_event_accepts_exact_schema():
    ev = StatusEvent(job_id="j1", state="running", pct=0.4,
                     node_id="n1", elapsed_ms=1200, stage="inference")
    assert ev.state == "running"


def test_status_event_rejects_content_fields():
    # TR-28: the metadata-only rule is enforced in code, not convention
    with pytest.raises(ValidationError):
        StatusEvent(job_id="j1", state="running", pct=0.4, node_id="n1",
                    elapsed_ms=1, stage="inference",
                    placeholdered_prompt="⟦PERSON_1⟧ owes rent")


def test_job_carries_no_customer_identity_field():
    assert "customer_id" not in Job.model_fields
    assert "raw_payload" not in Job.model_fields


def test_enroll_request_shape():
    req = EnrollRequest(enrollment_token="t", node_public_key="ab12",
                        capabilities=Capabilities(models=["m"], max_context=4096,
                                                  throughput_hint="laptop"),
                        endpoint_info="outbound-only")
    assert req.capabilities.models == ["m"]


def test_result_and_heartbeat_reject_extras():
    with pytest.raises(ValidationError):
        Result(job_id="j", placeholdered_completion="c", node_id="n",
               node_signature="s", completed_at=1.0, raw="nope")
    with pytest.raises(ValidationError):
        HeartbeatRequest(node_id="n", signature="s", current_load=0, extra=1)
