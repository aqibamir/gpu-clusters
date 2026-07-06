from cmndr.detect import WordlistDetector
from cmndr.routers.heuristic import HeuristicRouter
from cmndr.types import RequestItem


def det():
    return WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})


def test_pii_bearing_payload_escalates():
    r = HeuristicRouter(det())
    d = r.classify(RequestItem("r1", "summarize", "Summarize: Jane Smith of Acme Corp owes rent."))
    assert d.route == "escalate"
    assert 0.0 <= d.confidence <= 1.0
    assert d.classifier_version == "heuristic-v1"


def test_short_clean_payload_stays_local():
    r = HeuristicRouter(det())
    d = r.classify(RequestItem("r2", "summarize", "what is 2+2"))
    assert d.route == "local"
    assert d.confidence >= 0.7


def test_sensitivity_hint_high_always_escalates():
    r = HeuristicRouter(det())
    d = r.classify(RequestItem("r3", "summarize", "hello", sensitivity_hint="high"))
    assert d.route == "escalate"


def test_long_payload_escalates():
    r = HeuristicRouter(det())
    d = r.classify(RequestItem("r4", "summarize", "word " * 900))
    assert d.route == "escalate"


def test_threshold_is_configurable_per_task_type():
    # an impossible threshold forces even clean short payloads to escalate
    r = HeuristicRouter(det(), thresholds={"summarize": 1.01})
    d = r.classify(RequestItem("r5", "summarize", "what is 2+2"))
    assert d.route == "escalate"
    # other task types fall back to default threshold and stay local
    d2 = r.classify(RequestItem("r6", "classify", "what is 2+2"))
    assert d2.route == "local"


def test_every_item_gets_decision_and_confidence():
    r = HeuristicRouter(det())
    for i, payload in enumerate(["", "short", "Jane Smith", "x " * 2000]):
        d = r.classify(RequestItem(f"b{i}", "summarize", payload))
        assert d.route in ("local", "escalate")
        assert 0.0 <= d.confidence <= 1.0
