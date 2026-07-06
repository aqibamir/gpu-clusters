"""TR-2: router calibration baseline — "coarse but done". Escalate-recall is
the safety-critical number: a missed escalation sends a hard/PII request to a
weak local answer; a missed local costs tokens, not privacy."""

import json
from dataclasses import dataclass
from pathlib import Path

from cmndr.types import RequestItem


@dataclass
class CalibrationReport:
    escalate_recall: float
    local_precision: float
    n: int
    confusion: dict[str, int]


def load_routing_samples(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def measure_calibration(router, samples: list[dict]) -> CalibrationReport:
    tp = fp = tn = fn = 0  # positive class = escalate
    for i, s in enumerate(samples):
        d = router.classify(RequestItem(f"cal{i}", s["task_type"], s["payload"]))
        gold, got = s["gold_route"], d.route
        if gold == "escalate" and got == "escalate":
            tp += 1
        elif gold == "local" and got == "escalate":
            fp += 1
        elif gold == "local" and got == "local":
            tn += 1
        else:
            fn += 1
    escalate_recall = tp / (tp + fn) if (tp + fn) else 1.0
    local_precision = tn / (tn + fn) if (tn + fn) else 1.0
    return CalibrationReport(escalate_recall=escalate_recall,
                             local_precision=local_precision,
                             n=len(samples),
                             confusion={"tp": tp, "fp": fp, "tn": tn, "fn": fn})


def main() -> None:
    from cmndr.detectors.presidio import PresidioDetector, spacy_model_available
    from cmndr.detect import WordlistDetector
    from cmndr.routers.heuristic import HeuristicRouter

    model = "en_core_web_sm"
    if spacy_model_available(model):
        detector, det_name = PresidioDetector(model=model), f"Presidio ({model})"
    else:
        detector, det_name = WordlistDetector({}), "Wordlist (empty — WARNING: no model)"
    router = HeuristicRouter(detector)
    samples = load_routing_samples("eval/datasets/routing_labeled.jsonl")
    r = measure_calibration(router, samples)
    out = Path("eval/reports/calibration.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# TR-2 Calibration Report\n\n"
        f"- Router: heuristic-v1 (default threshold 0.7)  ·  Detector: {det_name}\n"
        f"- Samples: {r.n}\n"
        f"- **Escalate-recall: {r.escalate_recall:.2%}**\n"
        f"- Local-precision: {r.local_precision:.2%}\n"
        f"- Confusion (positive=escalate): {r.confusion}\n\n"
        "No target required for MVP; the number must be known (TR-2). Re-run on\n"
        "every router or detector change.\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
