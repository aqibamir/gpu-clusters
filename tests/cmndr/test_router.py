from cmndr.types import RequestItem
from cmndr.router import Router, ThresholdRouter


def test_high_sensitivity_escalates():
    r: Router = ThresholdRouter(threshold=0.7)
    d = r.classify(RequestItem(id="r1", task_type="summarize",
                               payload="x", sensitivity_hint="high"))
    assert d.route == "escalate"
    assert d.confidence < 0.7
    assert d.classifier_version == "stub-v0"


def test_low_sensitivity_stays_local():
    r = ThresholdRouter(threshold=0.7)
    d = r.classify(RequestItem(id="r2", task_type="summarize",
                               payload="x", sensitivity_hint="low"))
    assert d.route == "local"
    assert d.request_id == "r2"
