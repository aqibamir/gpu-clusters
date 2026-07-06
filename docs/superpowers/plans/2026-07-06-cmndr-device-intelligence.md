# cmndr Device Intelligence Implementation Plan (Plan 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the device pipeline's test stubs with the real components — Presidio detector with measured coverage (TR-5), heuristic router with configurable per-task thresholds and a calibration report (TR-1/2), append-only audit log (TR-8), device challenge verifier (TR-10), and the real llama.cpp local backend (TR-9/M6).

**Architecture:** Everything slots behind the seams Plan 1 froze: `Detector`, `Router`, `Backend` protocols and the `Pipeline` entry point. New modules `cmndr/detectors/presidio.py`, `cmndr/routers/heuristic.py`, `cmndr/audit.py`, `cmndr/challenge.py`, `cmndr/backends/llamacpp.py`, plus an `eval/` harness with labeled datasets producing the TR-2/TR-5 reports. `Pipeline` gains audit + challenge wiring.

**Tech Stack:** Python 3.11+, pytest, spaCy + presidio-analyzer (already deps), llama-cpp-python\[metal\] (optional dep), JSONL for audit + datasets.

## Global Constraints

- Python ≥ 3.11; package layout under `cmndr/`; tests under `tests/cmndr/`.
- Placeholder format `⟦TYPE_N⟧` (U+27E6/U+27E7), 1-indexed per type, identical values reuse placeholders.
- `RequestItem.payload` and `RedactionMap` MUST have no serialization path across the boundary; the audit log stores neither raw values nor the map (TR-8).
- Every code-bearing task is TDD: failing test → run-fail → minimal impl → run-pass → commit.
- Tests needing spaCy models or llama.cpp are guarded with `pytest.mark.skipif` so the suite passes on machines without them.
- Run tests with `uv run pytest …` from the repo root.
- Do not import from the legacy `anonymizer/` package in new code; it is reference only and is deleted in Plan 4's cleanup task.

---

### Task 1: PresidioDetector behind the Detector seam

**Files:**
- Create: `cmndr/detectors/__init__.py` (empty)
- Create: `cmndr/detectors/presidio.py`
- Test: `tests/cmndr/test_presidio_detector.py`

**Interfaces:**
- Consumes: `cmndr.types.Entity`, `cmndr.detect.Detector` protocol.
- Produces: `PresidioDetector(model: str = "en_core_web_sm", entity_types: list[str] | None = None)` with `detect(text: str) -> list[Entity]`. Module-level `DEFAULT_ENTITY_TYPES: list[str]` and helper `spacy_model_available(model: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_presidio_detector.py
import pytest

from cmndr.detectors.presidio import PresidioDetector, spacy_model_available

MODEL = "en_core_web_sm"
needs_model = pytest.mark.skipif(
    not spacy_model_available(MODEL),
    reason=f"spaCy model {MODEL} not installed",
)


@needs_model
def test_detects_person_and_email():
    det = PresidioDetector(model=MODEL)
    ents = det.detect("Contact Jane Smith at jane.smith@acme.example please")
    types = {e.entity_type for e in ents}
    assert "PERSON" in types
    assert "EMAIL_ADDRESS" in types
    # spans must slice back to the reported text
    for e in ents:
        assert "Contact Jane Smith at jane.smith@acme.example please"[e.start:e.end] == e.text


@needs_model
def test_entities_sorted_by_start():
    det = PresidioDetector(model=MODEL)
    ents = det.detect("Email jane@acme.example about Bob Jones.")
    starts = [e.start for e in ents]
    assert starts == sorted(starts)


def test_model_available_helper_false_for_nonsense():
    assert spacy_model_available("no_such_model_xyz") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_presidio_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.detectors'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/detectors/presidio.py
"""Presidio-backed Detector (§6.3). Salvaged from anonymizer/pipeline.py but
implementing the cmndr Detector seam; returns Entity spans, never mutates text."""

from functools import lru_cache

from cmndr.types import Entity

DEFAULT_ENTITY_TYPES = [
    "PERSON", "ORG", "LOCATION", "DATE_TIME", "PHONE_NUMBER",
    "EMAIL_ADDRESS", "IBAN_CODE", "CREDIT_CARD", "US_SSN", "MONEY", "NRP",
]


def spacy_model_available(model: str) -> bool:
    try:
        import spacy.util
        return spacy.util.is_package(model)
    except Exception:
        return False


@lru_cache(maxsize=2)
def _analyzer(model: str):
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import SpacyNlpEngine
    nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": model}])
    return AnalyzerEngine(nlp_engine=nlp_engine)


class PresidioDetector:
    def __init__(self, model: str = "en_core_web_sm",
                 entity_types: list[str] | None = None) -> None:
        self._model = model
        self._entity_types = entity_types or DEFAULT_ENTITY_TYPES

    def detect(self, text: str) -> list[Entity]:
        results = _analyzer(self._model).analyze(
            text=text, entities=self._entity_types, language="en")
        ents = [Entity(entity_type=r.entity_type, start=r.start, end=r.end,
                       text=text[r.start:r.end]) for r in results]
        ents.sort(key=lambda e: e.start)
        return ents
```

- [ ] **Step 4: Ensure the spaCy model is installed, then run tests**

Run: `uv run python -m spacy download en_core_web_sm || uv run pip install en_core_web_sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`
Then: `uv run pytest tests/cmndr/test_presidio_detector.py -v`
Expected: PASS (3 tests; if the model genuinely cannot install, 2 skip and 1 passes — do not proceed with skips without noting it)

- [ ] **Step 5: Commit**

```bash
git add cmndr/detectors tests/cmndr/test_presidio_detector.py
git commit -m "feat(cmndr): Presidio detector behind the Detector seam (§6.3)"
```

---

### Task 2: Coverage measurement harness (TR-5)

**Files:**
- Create: `eval/__init__.py` (empty)
- Create: `eval/datasets/pii_labeled.jsonl`
- Create: `eval/coverage.py`
- Test: `tests/cmndr/test_coverage_harness.py`

**Interfaces:**
- Consumes: `Detector` protocol (works with `WordlistDetector` in tests, `PresidioDetector` for the real report).
- Produces: `measure_coverage(detector, samples: list[dict]) -> CoverageReport` where `CoverageReport` is a dataclass with `recall: float`, `false_positives: int`, `gold_total: int`, `detected_matched: int`; `load_samples(path) -> list[dict]`; CLI `python -m eval.coverage` writing `eval/reports/coverage.md`.

- [ ] **Step 1: Create the labeled dataset**

Create `eval/datasets/pii_labeled.jsonl` — one JSON object per line, `{"text": ..., "gold": [{"entity_type": ..., "text": ...}]}`. 20 lines covering PERSON, ORG, EMAIL_ADDRESS, PHONE_NUMBER, IBAN_CODE, LOCATION, DATE_TIME. Content (write all 20 lines exactly):

```jsonl
{"text": "Jane Smith signed the contract with Acme Corp on 12 March 2026.", "gold": [{"entity_type": "PERSON", "text": "Jane Smith"}, {"entity_type": "ORG", "text": "Acme Corp"}, {"entity_type": "DATE_TIME", "text": "12 March 2026"}]}
{"text": "Please wire the fee to IBAN DE89370400440532013000 by Friday.", "gold": [{"entity_type": "IBAN_CODE", "text": "DE89370400440532013000"}, {"entity_type": "DATE_TIME", "text": "Friday"}]}
{"text": "Contact Bob Jones at bob.jones@example.com or +49 30 901820.", "gold": [{"entity_type": "PERSON", "text": "Bob Jones"}, {"entity_type": "EMAIL_ADDRESS", "text": "bob.jones@example.com"}, {"entity_type": "PHONE_NUMBER", "text": "+49 30 901820"}]}
{"text": "The meeting with Maria Garcia takes place in Berlin next Tuesday.", "gold": [{"entity_type": "PERSON", "text": "Maria Garcia"}, {"entity_type": "LOCATION", "text": "Berlin"}, {"entity_type": "DATE_TIME", "text": "next Tuesday"}]}
{"text": "Invoice 4711 was issued to Mustermann GmbH for 12,000 EUR.", "gold": [{"entity_type": "ORG", "text": "Mustermann GmbH"}, {"entity_type": "MONEY", "text": "12,000 EUR"}]}
{"text": "Dr. Anna Weber will review the file before 30 June 2026.", "gold": [{"entity_type": "PERSON", "text": "Anna Weber"}, {"entity_type": "DATE_TIME", "text": "30 June 2026"}]}
{"text": "Send the report to legal@lachaine.example and cc Tom Baker.", "gold": [{"entity_type": "EMAIL_ADDRESS", "text": "legal@lachaine.example"}, {"entity_type": "PERSON", "text": "Tom Baker"}]}
{"text": "Our office in Munich handles all requests from Bavaria.", "gold": [{"entity_type": "LOCATION", "text": "Munich"}, {"entity_type": "LOCATION", "text": "Bavaria"}]}
{"text": "The card 4111111111111111 was charged twice on 2026-01-15.", "gold": [{"entity_type": "CREDIT_CARD", "text": "4111111111111111"}, {"entity_type": "DATE_TIME", "text": "2026-01-15"}]}
{"text": "Peter Schmidt from Hamburg called about the delayed shipment.", "gold": [{"entity_type": "PERSON", "text": "Peter Schmidt"}, {"entity_type": "LOCATION", "text": "Hamburg"}]}
{"text": "Reach Susanne Vogel at s.vogel@firma.example for scheduling.", "gold": [{"entity_type": "PERSON", "text": "Susanne Vogel"}, {"entity_type": "EMAIL_ADDRESS", "text": "s.vogel@firma.example"}]}
{"text": "The vendor Initech Ltd missed the deadline of 1 May 2026.", "gold": [{"entity_type": "ORG", "text": "Initech Ltd"}, {"entity_type": "DATE_TIME", "text": "1 May 2026"}]}
{"text": "Employee John Doe earns 85,000 EUR per year.", "gold": [{"entity_type": "PERSON", "text": "John Doe"}, {"entity_type": "MONEY", "text": "85,000 EUR"}]}
{"text": "Call our Frankfurt branch at +49 69 1234567 tomorrow.", "gold": [{"entity_type": "LOCATION", "text": "Frankfurt"}, {"entity_type": "PHONE_NUMBER", "text": "+49 69 1234567"}, {"entity_type": "DATE_TIME", "text": "tomorrow"}]}
{"text": "Karin Lang transferred funds to IBAN AT611904300234573201 yesterday.", "gold": [{"entity_type": "PERSON", "text": "Karin Lang"}, {"entity_type": "IBAN_CODE", "text": "AT611904300234573201"}, {"entity_type": "DATE_TIME", "text": "yesterday"}]}
{"text": "The claimant, Mr. Oliver Stone, resides in Vienna.", "gold": [{"entity_type": "PERSON", "text": "Oliver Stone"}, {"entity_type": "LOCATION", "text": "Vienna"}]}
{"text": "Globex Corporation acquired the startup for 2.5 million dollars.", "gold": [{"entity_type": "ORG", "text": "Globex Corporation"}, {"entity_type": "MONEY", "text": "2.5 million dollars"}]}
{"text": "Her assistant emailed contact@globex.example on 3 February 2026.", "gold": [{"entity_type": "EMAIL_ADDRESS", "text": "contact@globex.example"}, {"entity_type": "DATE_TIME", "text": "3 February 2026"}]}
{"text": "Werner Krause of Stuttgart chairs the audit committee.", "gold": [{"entity_type": "PERSON", "text": "Werner Krause"}, {"entity_type": "LOCATION", "text": "Stuttgart"}]}
{"text": "Payment of 300 EUR is due to Nordwind AG by 15 August 2026.", "gold": [{"entity_type": "MONEY", "text": "300 EUR"}, {"entity_type": "ORG", "text": "Nordwind AG"}, {"entity_type": "DATE_TIME", "text": "15 August 2026"}]}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/cmndr/test_coverage_harness.py
from cmndr.detect import WordlistDetector
from eval.coverage import measure_coverage, load_samples


def test_perfect_detector_scores_full_recall():
    samples = [{"text": "Jane Smith works at Acme Corp",
                "gold": [{"entity_type": "PERSON", "text": "Jane Smith"},
                         {"entity_type": "ORG", "text": "Acme Corp"}]}]
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    report = measure_coverage(det, samples)
    assert report.recall == 1.0
    assert report.false_positives == 0
    assert report.gold_total == 2


def test_missing_entity_lowers_recall_and_extra_counts_fp():
    samples = [{"text": "Jane Smith works at Acme Corp",
                "gold": [{"entity_type": "PERSON", "text": "Jane Smith"},
                         {"entity_type": "ORG", "text": "Acme Corp"}]}]
    det = WordlistDetector({"Jane Smith": "PERSON", "works": "VERB"})
    report = measure_coverage(det, samples)
    assert report.recall == 0.5
    assert report.false_positives == 1


def test_load_samples_reads_jsonl():
    samples = load_samples("eval/datasets/pii_labeled.jsonl")
    assert len(samples) == 20
    assert all("text" in s and "gold" in s for s in samples)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_coverage_harness.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval'`

- [ ] **Step 4: Write minimal implementation**

```python
# eval/coverage.py
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
            # a hit if the detected span text overlaps a gold value (type-agnostic
            # on ORG/PERSON confusions would hide real misses — require type match)
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
```

Also create empty `eval/__init__.py`.

- [ ] **Step 5: Run tests, then generate the real report**

Run: `uv run pytest tests/cmndr/test_coverage_harness.py -v`
Expected: PASS (3 tests)
Then: `uv run python -m eval.coverage`
Expected: prints the report; `eval/reports/coverage.md` exists with a measured recall figure (any number — TR-5 requires the figure to exist, not to hit a target).

- [ ] **Step 6: Commit**

```bash
git add eval tests/cmndr/test_coverage_harness.py
git commit -m "feat(eval): TR-5 coverage harness + labeled PII set + report"
```

---

### Task 3: HeuristicRouter with per-task-type thresholds (TR-1)

**Files:**
- Create: `cmndr/routers/__init__.py` (empty)
- Create: `cmndr/routers/heuristic.py`
- Test: `tests/cmndr/test_heuristic_router.py`

**Interfaces:**
- Consumes: `Detector` protocol, `RequestItem`, `RoutingDecision` from `cmndr.types`.
- Produces: `HeuristicRouter(detector, thresholds: dict[str, float] | None = None, default_threshold: float = 0.7, version: str = "heuristic-v1")` implementing `classify(item) -> RoutingDecision`. Decision rule per §6.2: compute a local-confidence score; `local` iff score ≥ threshold for the item's task_type, else `escalate`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_heuristic_router.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_heuristic_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.routers'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/routers/heuristic.py
"""TR-1 router, bootstrap heuristic (Open Decision D4). Score = how confident
we are the LOCAL model suffices. local iff score >= threshold(task_type);
erring toward escalation is safe because the sandwich protects escalated
traffic (§6.2). PII presence *lowers* local-confidence: those requests go
through anonymization + preview rather than a weak local answer."""

from cmndr.detect import Detector
from cmndr.types import RequestItem, RoutingDecision

VERSION = "heuristic-v1"
LONG_PAYLOAD_WORDS = 400


class HeuristicRouter:
    def __init__(self, detector: Detector,
                 thresholds: dict[str, float] | None = None,
                 default_threshold: float = 0.7,
                 version: str = VERSION) -> None:
        self._detector = detector
        self._thresholds = thresholds or {}
        self._default_threshold = default_threshold
        self._version = version

    def threshold_for(self, task_type: str) -> float:
        return self._thresholds.get(task_type, self._default_threshold)

    def classify(self, item: RequestItem) -> RoutingDecision:
        score = 0.9  # start confident the local model suffices
        if item.sensitivity_hint == "high":
            score = 0.0
        else:
            words = len(item.payload.split())
            if words > LONG_PAYLOAD_WORDS:
                score -= 0.5
            entities = self._detector.detect(item.payload)
            if entities:
                score -= 0.3 + 0.05 * min(len(entities), 6)
        score = max(0.0, min(1.0, score))
        threshold = self.threshold_for(item.task_type)
        route = "local" if score >= threshold else "escalate"
        # confidence reports certainty in the *chosen* route
        confidence = score if route == "local" else 1.0 - score
        return RoutingDecision(request_id=item.id, route=route,
                               confidence=confidence,
                               classifier_version=self._version)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cmndr/test_heuristic_router.py -v`
Expected: PASS (6 tests). Also run the full suite: `uv run pytest tests/cmndr -q` — the Plan-1 `ThresholdRouter` tests must still pass (it remains for tests/fakes).

- [ ] **Step 5: Commit**

```bash
git add cmndr/routers tests/cmndr/test_heuristic_router.py
git commit -m "feat(cmndr): heuristic router with per-task-type thresholds (TR-1)"
```

---

### Task 4: Calibration harness + report (TR-2)

**Files:**
- Create: `eval/datasets/routing_labeled.jsonl`
- Create: `eval/calibration.py`
- Test: `tests/cmndr/test_calibration_harness.py`

**Interfaces:**
- Consumes: `Router` protocol (`classify`), `RequestItem`.
- Produces: `measure_calibration(router, samples) -> CalibrationReport` (dataclass: `escalate_recall: float`, `local_precision: float`, `n: int`, `confusion: dict[str, int]` with keys `"tp","fp","tn","fn"` where positive = escalate); CLI `python -m eval.calibration` writing `eval/reports/calibration.md`.

- [ ] **Step 1: Create the labeled routing dataset**

Create `eval/datasets/routing_labeled.jsonl` — `{"task_type": ..., "payload": ..., "gold_route": "local"|"escalate"}`. 16 lines (representative SME batch mix; PII or length ⇒ escalate, trivial/clean ⇒ local):

```jsonl
{"task_type": "summarize", "payload": "Summarize: Jane Smith of Acme Corp owes 3 months rent.", "gold_route": "escalate"}
{"task_type": "summarize", "payload": "what is 2+2", "gold_route": "local"}
{"task_type": "summarize", "payload": "Summarize the attached contract between Mustermann GmbH and Globex Corporation regarding delivery terms.", "gold_route": "escalate"}
{"task_type": "summarize", "payload": "rewrite this sentence in passive voice: the cat ate the mouse", "gold_route": "local"}
{"task_type": "classify", "payload": "Is this spam? 'You won a prize, click here'", "gold_route": "local"}
{"task_type": "summarize", "payload": "Contact Bob Jones at bob.jones@example.com about invoice 4711.", "gold_route": "escalate"}
{"task_type": "summarize", "payload": "translate 'good morning' to German", "gold_route": "local"}
{"task_type": "extract", "payload": "Extract parties: Karin Lang and Nordwind AG signed on 15 August 2026.", "gold_route": "escalate"}
{"task_type": "summarize", "payload": "list three synonyms for 'fast'", "gold_route": "local"}
{"task_type": "summarize", "payload": "Peter Schmidt from Hamburg called about IBAN DE89370400440532013000.", "gold_route": "escalate"}
{"task_type": "classify", "payload": "what day comes after Tuesday", "gold_route": "local"}
{"task_type": "summarize", "payload": "Dr. Anna Weber will review employee John Doe's salary of 85,000 EUR.", "gold_route": "escalate"}
{"task_type": "summarize", "payload": "how many grams in a kilogram", "gold_route": "local"}
{"task_type": "extract", "payload": "Extract the email: reach legal@lachaine.example for terms.", "gold_route": "escalate"}
{"task_type": "summarize", "payload": "give me a haiku about rain", "gold_route": "local"}
{"task_type": "summarize", "payload": "Werner Krause of Stuttgart chairs the audit committee at Initech Ltd.", "gold_route": "escalate"}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/cmndr/test_calibration_harness.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_calibration_harness.py -v`
Expected: FAIL with `ImportError: cannot import name 'measure_calibration'` (or ModuleNotFoundError)

- [ ] **Step 4: Write minimal implementation**

```python
# eval/calibration.py
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
```

- [ ] **Step 5: Run tests, then generate the real report**

Run: `uv run pytest tests/cmndr/test_calibration_harness.py -v`
Expected: PASS (3 tests)
Then: `uv run python -m eval.calibration`
Expected: `eval/reports/calibration.md` exists with a measured escalate-recall.

- [ ] **Step 6: Commit**

```bash
git add eval tests/cmndr/test_calibration_harness.py
git commit -m "feat(eval): TR-2 calibration harness + routing set + report"
```

---

### Task 5: Append-only audit log (TR-8)

**Files:**
- Create: `cmndr/audit.py`
- Test: `tests/cmndr/test_audit.py`

**Interfaces:**
- Consumes: `RoutingDecision`, `PlaceholderedPayload`, `Response` from `cmndr.types`.
- Produces: `AuditLog(path: str | Path)` with `record_routing(decision: RoutingDecision) -> None`, `record_escalation(request_id: str, target: str, payload: PlaceholderedPayload, preview_accepted: bool, restoration_anomalies: list[str]) -> None`, `entries() -> list[dict]`. JSONL on disk; every entry gets `ts` (epoch float) and `kind` (`"routing"` | `"escalation"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_audit.py
import json

from cmndr.audit import AuditLog
from cmndr.types import PlaceholderedPayload, RoutingDecision


def test_routing_entries_are_appended(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record_routing(RoutingDecision("r1", "local", 0.9, "heuristic-v1"))
    log.record_routing(RoutingDecision("r2", "escalate", 0.8, "heuristic-v1"))
    entries = log.entries()
    assert [e["request_id"] for e in entries] == ["r1", "r2"]
    assert entries[0]["kind"] == "routing"
    assert "ts" in entries[0]


def test_escalation_entry_holds_placeholders_never_raw_values(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    payload = PlaceholderedPayload("r3", "⟦PERSON_1⟧ of ⟦ORG_1⟧ owes rent",
                                   {"PERSON": 1, "ORG": 1})
    log.record_escalation("r3", target="cgn", payload=payload,
                          preview_accepted=True, restoration_anomalies=[])
    raw_file = (tmp_path / "audit.jsonl").read_text()
    assert "Jane Smith" not in raw_file          # no raw value anywhere
    e = log.entries()[0]
    assert e["kind"] == "escalation"
    assert e["placeholdered_payload"] == "⟦PERSON_1⟧ of ⟦ORG_1⟧ owes rent"
    assert e["entity_summary"] == {"PERSON": 1, "ORG": 1}
    assert e["preview_accepted"] is True


def test_append_only_across_instances(tmp_path):
    p = tmp_path / "audit.jsonl"
    AuditLog(p).record_routing(RoutingDecision("r1", "local", 0.9, "v"))
    AuditLog(p).record_routing(RoutingDecision("r2", "local", 0.9, "v"))
    assert len(AuditLog(p).entries()) == 2
    # file is plain JSONL — reconstructable without the class (N2)
    lines = p.read_text().splitlines()
    assert all(json.loads(line) for line in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.audit'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/audit.py
"""TR-8 / M4: local append-only audit log (Open Decision D3). Records every
routing decision and escalation in reconstructable form. NEVER stores raw
values or the RedactionMap — the two PII-bearing structures have no path in."""

import json
import time
from pathlib import Path

from cmndr.types import PlaceholderedPayload, RoutingDecision


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _append(self, entry: dict) -> None:
        entry["ts"] = time.time()
        with self._path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_routing(self, decision: RoutingDecision) -> None:
        self._append({
            "kind": "routing",
            "request_id": decision.request_id,
            "route": decision.route,
            "confidence": decision.confidence,
            "classifier_version": decision.classifier_version,
        })

    def record_escalation(self, request_id: str, target: str,
                          payload: PlaceholderedPayload, preview_accepted: bool,
                          restoration_anomalies: list[str]) -> None:
        self._append({
            "kind": "escalation",
            "request_id": request_id,
            "target": target,
            "placeholdered_payload": payload.text,
            "entity_summary": payload.entity_summary,
            "preview_accepted": preview_accepted,
            "restoration_anomalies": restoration_anomalies,
        })

    def entries(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines() if line.strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cmndr/test_audit.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add cmndr/audit.py tests/cmndr/test_audit.py
git commit -m "feat(cmndr): append-only audit log, no raw PII path (TR-8)"
```

---

### Task 6: Device challenge verifier (TR-10)

**Files:**
- Create: `cmndr/challenge.py`
- Test: `tests/cmndr/test_challenge.py`

**Interfaces:**
- Consumes: `RedactionMap`.
- Produces: `verify_no_leakage(outbound_text: str, redaction_map: RedactionMap) -> list[str]` returning the leaked original values (empty = pass); `LeakageDetected(Exception)` with `.leaked: list[str]`; `assert_no_leakage(outbound_text, redaction_map) -> None` raising `LeakageDetected` on any hit.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_challenge.py
import pytest

from cmndr.challenge import LeakageDetected, assert_no_leakage, verify_no_leakage
from cmndr.types import RedactionEntry, RedactionMap


def rmap() -> RedactionMap:
    m = RedactionMap()
    m.entries.append(RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON", (0, 10), "detector"))
    m.entries.append(RedactionEntry("⟦ORG_1⟧", "Acme Corp", "ORG", (14, 23), "detector"))
    return m


def test_clean_payload_passes():
    assert verify_no_leakage("⟦PERSON_1⟧ of ⟦ORG_1⟧ owes rent", rmap()) == []


def test_corrupted_payload_fails_and_names_the_leak():
    leaked = verify_no_leakage("Jane Smith of ⟦ORG_1⟧ owes rent", rmap())
    assert leaked == ["Jane Smith"]


def test_assert_raises_with_leak_list():
    with pytest.raises(LeakageDetected) as exc:
        assert_no_leakage("Jane Smith of Acme Corp", rmap())
    assert set(exc.value.leaked) == {"Jane Smith", "Acme Corp"}


def test_user_removed_redactions_are_not_leaks():
    # a value the user deliberately un-redacted is no longer in the map,
    # so its presence is not flagged — the map is the source of truth
    m = rmap()
    m.entries = [e for e in m.entries if e.placeholder != "⟦ORG_1⟧"]
    assert verify_no_leakage("⟦PERSON_1⟧ of Acme Corp owes rent", m) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_challenge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.challenge'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/challenge.py
"""TR-10: on-device challenge — confirm outbound traffic for a request contains
none of its original entity values. Runs after preview-accept, before dispatch.
Structural backstop for the boundary invariant (TR-7)."""

from cmndr.types import RedactionMap


class LeakageDetected(Exception):
    def __init__(self, leaked: list[str]) -> None:
        self.leaked = leaked
        super().__init__(f"raw entity values in outbound payload: {leaked}")


def verify_no_leakage(outbound_text: str, redaction_map: RedactionMap) -> list[str]:
    return [e.original_value for e in redaction_map.entries
            if e.original_value in outbound_text]


def assert_no_leakage(outbound_text: str, redaction_map: RedactionMap) -> None:
    leaked = verify_no_leakage(outbound_text, redaction_map)
    if leaked:
        raise LeakageDetected(leaked)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cmndr/test_challenge.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cmndr/challenge.py tests/cmndr/test_challenge.py
git commit -m "feat(cmndr): device challenge verifier (TR-10)"
```

---

### Task 7: Wire audit + challenge into the Pipeline

**Files:**
- Modify: `cmndr/pipeline.py`
- Test: `tests/cmndr/test_pipeline_audit_challenge.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Pipeline(router, anonymizer, local_backend, provider, audit: AuditLog | None = None)`. Behavior: every `process_item` records a routing entry; every escalation records an escalation entry (after the provider call, with anomalies); `assert_no_leakage(preview.payload.text, preview.redaction_map)` runs after preview-accept, before the provider call. Existing constructor calls without `audit` keep working.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_pipeline_audit_challenge.py
import pytest

from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.fake import EchoBackend
from cmndr.challenge import LeakageDetected
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.fake import EchoProvider
from cmndr.router import ThresholdRouter
from cmndr.types import RequestItem


def make_pipeline(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    pipe = Pipeline(
        router=ThresholdRouter(),
        anonymizer=Anonymizer(WordlistDetector({"Jane Smith": "PERSON"})),
        local_backend=EchoBackend(),
        provider=EchoProvider(),
        audit=audit,
    )
    return pipe, audit


def test_local_route_writes_routing_entry(tmp_path):
    pipe, audit = make_pipeline(tmp_path)
    pipe.process_item(RequestItem("r1", "summarize", "what is 2+2", sensitivity_hint="low"))
    kinds = [e["kind"] for e in audit.entries()]
    assert kinds == ["routing"]


def test_escalation_writes_routing_and_escalation_entries(tmp_path):
    pipe, audit = make_pipeline(tmp_path)
    pipe.process_item(
        RequestItem("r2", "summarize", "Jane Smith owes rent", sensitivity_hint="high"),
        approve=lambda p: p.accept())
    entries = audit.entries()
    assert [e["kind"] for e in entries] == ["routing", "escalation"]
    esc = entries[1]
    assert "Jane Smith" not in esc["placeholdered_payload"]
    assert esc["preview_accepted"] is True


def test_leaking_preview_edit_is_blocked_before_provider(tmp_path):
    pipe, audit = make_pipeline(tmp_path)

    def approve_and_unredact(p):
        p.remove_redaction("⟦PERSON_1⟧")  # user removes the redaction...
        p.redaction_map.entries.append(     # ...but the map says it must be hidden
            __import__("cmndr.types", fromlist=["RedactionEntry"]).RedactionEntry(
                "⟦PERSON_1⟧", "Jane Smith", "PERSON", (0, 10), "detector"))
        p.accept()

    with pytest.raises(LeakageDetected):
        pipe.process_item(
            RequestItem("r3", "summarize", "Jane Smith owes rent", sensitivity_hint="high"),
            approve=approve_and_unredact)


def test_audit_is_optional_default_none(tmp_path):
    pipe = Pipeline(ThresholdRouter(),
                    Anonymizer(WordlistDetector({})),
                    EchoBackend(), EchoProvider())
    out = pipe.process_item(RequestItem("r4", "summarize", "hi", sensitivity_hint="low"))
    assert out.route == "local"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_pipeline_audit_challenge.py -v`
Expected: FAIL with `TypeError: Pipeline.__init__() got an unexpected keyword argument 'audit'`

- [ ] **Step 3: Update the Pipeline**

Replace the full contents of `cmndr/pipeline.py` with:

```python
from typing import Callable, Optional
from cmndr.types import RequestItem, Response
from cmndr.router import Router
from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.base import Backend
from cmndr.challenge import assert_no_leakage
from cmndr.providers.base import Provider
from cmndr.preview import Preview, EscalationNotAccepted
from cmndr.restore import restore


class Pipeline:
    """Entry Point (M7). Wires route → [local] | [anonymize → preview → challenge
    → provider → restore]. Only `preview.payload` ever reaches the provider — the
    boundary invariant is structural, not conventional. Every decision is audited
    (TR-8); outbound text is challenge-verified after preview-accept (TR-10)."""

    def __init__(self, router: Router, anonymizer: Anonymizer,
                 local_backend: Backend, provider: Provider,
                 audit: Optional[AuditLog] = None) -> None:
        self._router = router
        self._anonymizer = anonymizer
        self._local = local_backend
        self._provider = provider
        self._audit = audit

    def process_item(self, item: RequestItem,
                     approve: Optional[Callable[[Preview], None]] = None) -> Response:
        decision = self._router.classify(item)
        if self._audit is not None:
            self._audit.record_routing(decision)

        if decision.route == "local":
            completion = self._local.infer(item.payload)
            return Response(item.id, "local", completion.text)

        payload, rmap = self._anonymizer.anonymize(item.id, item.payload)
        preview = Preview(payload, rmap)
        if approve is not None:
            approve(preview)
        if not preview.accepted:
            raise EscalationNotAccepted(item.id)

        assert_no_leakage(preview.payload.text, preview.redaction_map)  # TR-10

        completion = self._provider.infer(preview.payload)  # ONLY placeholdered text
        restored = restore(completion, preview.redaction_map)
        if self._audit is not None:
            self._audit.record_escalation(
                item.id, target="provider", payload=preview.payload,
                preview_accepted=True,
                restoration_anomalies=restored.restoration_anomalies)
        return Response(item.id, "escalate", restored.text,
                        restoration_anomalies=restored.restoration_anomalies)

    def process_batch(self, items: list[RequestItem],
                      approve: Optional[Callable[[Preview], None]] = None) -> list[Response]:
        return [self.process_item(it, approve=approve) for it in items]
```

- [ ] **Step 4: Run the full cmndr suite**

Run: `uv run pytest tests/cmndr -q`
Expected: PASS — all Plan-1 tests plus the 4 new ones. (Plan-1 pipeline tests pass unchanged because `audit` defaults to `None`.)

- [ ] **Step 5: Commit**

```bash
git add cmndr/pipeline.py tests/cmndr/test_pipeline_audit_challenge.py
git commit -m "feat(cmndr): pipeline audits every decision and challenge-verifies outbound (TR-8/10)"
```

---

### Task 8: LlamaCppBackend — the real Apple Silicon backend (TR-9 / M6)

**Files:**
- Create: `cmndr/backends/llamacpp.py`
- Test: `tests/cmndr/test_llamacpp_backend.py`

**Interfaces:**
- Consumes: `Backend` protocol, `Completion` from `cmndr.backends.base`.
- Produces: `LlamaCppBackend(model_path: str, n_ctx: int = 4096)` implementing `infer(prompt, max_tokens=512) -> Completion`; module helper `llamacpp_available() -> bool`; `DEFAULT_MODEL_PATH = "models/mistral-7b-instruct-v0.2.Q4_K_M.gguf"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_llamacpp_backend.py
from pathlib import Path

import pytest

from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                     llamacpp_available)

needs_llama = pytest.mark.skipif(
    not (llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()),
    reason="llama-cpp-python or GGUF model not available",
)


def test_available_helper_returns_bool():
    assert isinstance(llamacpp_available(), bool)


@needs_llama
def test_real_inference_returns_completion():
    backend = LlamaCppBackend(DEFAULT_MODEL_PATH)
    out = backend.infer("Answer with one word: what color is the sky?", max_tokens=8)
    assert out.text.strip()
    assert out.tokens_generated > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cmndr/test_llamacpp_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.backends.llamacpp'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/backends/llamacpp.py
"""M6: Apple Silicon local backend via llama.cpp/Metal. It is *a* backend,
not *the* backend (TR-9) — nothing Apple-specific leaks through Completion."""

from functools import lru_cache

from cmndr.backends.base import Completion

DEFAULT_MODEL_PATH = "models/mistral-7b-instruct-v0.2.Q4_K_M.gguf"


def llamacpp_available() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _load(model_path: str, n_ctx: int):
    from llama_cpp import Llama
    return Llama(model_path=model_path, n_ctx=n_ctx, verbose=False)


class LlamaCppBackend:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH, n_ctx: int = 4096) -> None:
        self._model_path = model_path
        self._n_ctx = n_ctx

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion:
        llm = _load(self._model_path, self._n_ctx)
        out = llm.create_completion(
            prompt=f"[INST] {prompt} [/INST]", max_tokens=max_tokens, temperature=0.2)
        text = out["choices"][0]["text"]
        return Completion(text=text, tokens_generated=out["usage"]["completion_tokens"])
```

- [ ] **Step 4: Install the optional dep if absent, run tests**

Run: `uv run python -c "import llama_cpp" 2>/dev/null || CMAKE_ARGS="-DGGML_METAL=on" uv pip install 'llama-cpp-python>=0.2.90'`
Then: `uv run pytest tests/cmndr/test_llamacpp_backend.py -v`
Expected: PASS (2 tests; the inference test may take ~30s on first model load). If llama-cpp cannot build on this machine, the inference test skips — note it in the commit message and proceed; EchoBackend remains the test backend.

- [ ] **Step 5: Commit**

```bash
git add cmndr/backends/llamacpp.py tests/cmndr/test_llamacpp_backend.py
git commit -m "feat(cmndr): llama.cpp Apple Silicon backend behind Backend seam (TR-9/M6)"
```

---

## Self-Review

**1. Spec coverage (this plan = §13 steps 4–5 + real detector/backend):**
- TR-1 (router + configurable threshold, below-threshold local → escalate) → Task 3. ✅
- TR-2 (calibration report with measured escalate-recall) → Task 4. ✅
- TR-5 (coverage + FP figures exist) → Task 2. ✅
- TR-8 (audit completeness, no raw values) → Tasks 5, 7. ✅
- TR-9/M6 (pluggable real backend) → Task 8. ✅
- TR-10 (challenge passes clean / fails corrupted) → Tasks 6, 7. ✅
- D2 (per-task-type threshold) → Task 3. D3 (local append-only) → Task 5. D4 (heuristic bootstrap) → Task 3. ✅
- TR-3/4/6/7 remain covered by Plan-1 tests, re-run in Tasks 3, 7.

**2. Placeholder scan:** no TBDs; every step has complete code, exact commands, expected results. ✅

**3. Type consistency:** `PresidioDetector.detect(text)->list[Entity]` matches the `Detector` protocol; `HeuristicRouter.classify(item)->RoutingDecision` matches `Router`; `AuditLog.record_escalation(request_id, target, payload, preview_accepted, restoration_anomalies)` is called with exactly those kwargs in Task 7; `assert_no_leakage(text, map)` signature consistent across Tasks 6/7; `LlamaCppBackend.infer(prompt, max_tokens)->Completion` matches `Backend`. ✅
