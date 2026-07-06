from cmndr.types import RequestItem, RoutingDecision
from eval.calibration import measure_calibration, load_routing_samples


class AlwaysEscalateRouter:
    def classify(self, item: RequestItem) -> RoutingDecision:
        return RoutingDecision(item.id, "escalate", 0.9, "fake")


class AlwaysLocalRouter:
    def classify(self, item: RequestItem) -> RoutingDecision:
        return RoutingDecision(item.id, "local", 0.9, "fake")


SAMPLES = [
    {"task_type": "summarize", "payload": "Jane Smith owes rent", "gold_route": "escalate"},
    {"task_type": "summarize", "payload": "what is 2+2", "gold_route": "local"},
]


def test_always_escalate_has_full_escalate_recall():
    r = measure_calibration(AlwaysEscalateRouter(), SAMPLES)
    assert r.escalate_recall == 1.0
    assert r.confusion["fp"] == 1


def test_always_local_has_zero_escalate_recall():
    r = measure_calibration(AlwaysLocalRouter(), SAMPLES)
    assert r.escalate_recall == 0.0
    assert r.confusion["fn"] == 1


def test_dataset_loads():
    samples = load_routing_samples("eval/datasets/routing_labeled.jsonl")
    assert len(samples) == 16
    assert all(s["gold_route"] in ("local", "escalate") for s in samples)
