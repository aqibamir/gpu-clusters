import pytest
from cmndr.types import RequestItem
from cmndr.router import ThresholdRouter
from cmndr.anonymizer import Anonymizer
from cmndr.detect import WordlistDetector
from cmndr.backends.fake import EchoBackend
from cmndr.providers.fake import EchoProvider, SpyProvider
from cmndr.preview import EscalationNotAccepted
from cmndr.pipeline import Pipeline


def _pipeline(provider=None):
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    return Pipeline(
        router=ThresholdRouter(threshold=0.7),
        anonymizer=Anonymizer(det),
        local_backend=EchoBackend(),
        provider=provider or EchoProvider(),
    )


def test_local_request_uses_local_backend():
    item = RequestItem("r1", "summarize", "hello", sensitivity_hint="low")
    resp = _pipeline().process_item(item)
    assert resp.route == "local"
    assert "[local] hello" in resp.text


def test_escalate_round_trip_restores_values():
    item = RequestItem("r2", "summarize", "Jane Smith joined Acme Corp",
                       sensitivity_hint="high")
    resp = _pipeline().process_item(item, approve=lambda p: p.accept())
    assert resp.route == "escalate"
    assert "Jane Smith" in resp.text and "Acme Corp" in resp.text


def test_escalate_without_accept_is_blocked():
    item = RequestItem("r3", "summarize", "Jane Smith", sensitivity_hint="high")
    with pytest.raises(EscalationNotAccepted):
        _pipeline().process_item(item)  # no approve callback → not accepted


def test_boundary_invariant_provider_never_sees_raw_pii():
    spy = SpyProvider()
    item = RequestItem("r4", "summarize", "Jane Smith joined Acme Corp",
                       sensitivity_hint="high")
    _pipeline(provider=spy).process_item(item, approve=lambda p: p.accept())
    assert len(spy.received) == 1
    sent = spy.received[0].text
    assert "Jane Smith" not in sent and "Acme Corp" not in sent


def test_process_batch_mixed_routes_with_shared_approve():
    items = [
        RequestItem("b1", "summarize", "hello", sensitivity_hint="low"),
        RequestItem("b2", "summarize", "Jane Smith joined Acme Corp",
                    sensitivity_hint="high"),
    ]
    responses = _pipeline().process_batch(items, approve=lambda p: p.accept())
    assert [r.request_id for r in responses] == ["b1", "b2"]
    assert responses[0].route == "local"
    assert responses[1].route == "escalate"
    assert "Jane Smith" in responses[1].text and "Acme Corp" in responses[1].text
