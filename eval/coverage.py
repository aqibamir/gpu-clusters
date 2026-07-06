"""TR-5: entity coverage + false-positive measurement against a labeled set.
The MVP accepts the coverage ceiling; it does not accept not knowing it."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CoverageReport:
    recall: float
    false_positives: int
    gold_total: int
    detected_matched: int


def load_samples(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def measure_coverage(detector, samples: list[dict]) -> CoverageReport:
    gold_total = 0
    matched = 0
    false_positives = 0
    for s in samples:
        detected = detector.detect(s["text"])
        gold = [(g["entity_type"], g["text"]) for g in s["gold"]]
        gold_total += len(gold)
        remaining = list(gold)
        for d in detected:
            # a hit requires a type match AND textual overlap; type-agnostic
            # matching would hide real ORG/PERSON confusions
            key = next((g for g in remaining
                        if g[0] == d.entity_type and (d.text in g[1] or g[1] in d.text)), None)
            if key is not None:
                remaining.remove(key)
                matched += 1
            else:
                false_positives += 1
    recall = matched / gold_total if gold_total else 1.0
    return CoverageReport(recall=recall, false_positives=false_positives,
                          gold_total=gold_total, detected_matched=matched)


def main() -> None:
    from cmndr.detectors.presidio import PresidioDetector, spacy_model_available
    model = "en_core_web_sm"
    if not spacy_model_available(model):
        print(f"spaCy model {model} not installed; cannot produce report", file=sys.stderr)
        sys.exit(1)
    samples = load_samples("eval/datasets/pii_labeled.jsonl")
    r = measure_coverage(PresidioDetector(model=model), samples)
    out = Path("eval/reports/coverage.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# TR-5 Coverage Report\n\n"
        f"- Detector: PresidioDetector ({model})\n"
        f"- Samples: {len(samples)}  ·  Gold entities: {r.gold_total}\n"
        f"- **Recall: {r.recall:.2%}** ({r.detected_matched}/{r.gold_total})\n"
        f"- False positives: {r.false_positives}\n\n"
        "MVP accepts this ceiling (§2.1); the preview gate (M3) is the backstop.\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
