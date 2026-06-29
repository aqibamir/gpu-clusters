# cmndr Device Pipeline Implementation Plan (Plan 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the on-device Zone-1 pipeline — route a request local-vs-escalate, anonymize escalating payloads into placeholders, gate them behind a pre-flight preview, send only placeholdered text to a (fake) escalation provider, and restore real values on return — entirely local, no network.

**Architecture:** A `cmndr` Python package with narrow interfaces (`Backend`, `Provider`, `Detector`, `Router`) implemented once for the MVP plus test fakes. A `Pipeline` (Entry Point) wires them: `route → [local backend] | [anonymize → preview-accept → provider → restore]`. The privacy boundary is structural — the `Provider` is only ever handed a `PlaceholderedPayload`, never raw text or the `RedactionMap`.

**Tech Stack:** Python 3.11+, pytest, dataclasses, pydantic (already in repo), spaCy + Presidio (already in repo, salvaged for the real detector).

## Global Constraints

- Python ≥ 3.11 (uses `Literal`, `tuple[int, int]`, `X | None` syntax).
- Package layout: new `cmndr/` package at repo root; tests under `tests/cmndr/`.
- Test runner: `pytest` (already configured via `pyproject.toml`).
- The two structures that hold real PII — `RequestItem.payload` and `RedactionMap` — MUST have no serialization path across the boundary. No task may add a method that hands either to a `Provider`.
- Placeholder format is `⟦TYPE_N⟧` (U+27E6 / U+27E7 brackets), type-tagged, 1-indexed per type, unique within a request; identical original values reuse the same placeholder.
- Every code-bearing task is TDD: failing test → run-fail → minimal impl → run-pass → commit.
- The existing `anonymizer/pipeline.py` is reference for the salvaged detector logic; do not import from it — the new code lives in `cmndr/`.

---

### Task 1: Data model (`cmndr/types.py`)

**Files:**
- Create: `cmndr/__init__.py` (empty)
- Create: `cmndr/types.py`
- Create: `tests/cmndr/__init__.py` (empty)
- Test: `tests/cmndr/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RequestItem`, `RoutingDecision`, `Entity`, `RedactionEntry`, `RedactionMap`, `PlaceholderedPayload`, `RestoreResult`, `Response`. Type aliases `Route = Literal["local","escalate"]`, `RedactionSource = Literal["detector","user-added","user-edited"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_types.py
from cmndr.types import (
    RequestItem, RoutingDecision, Entity, RedactionEntry, RedactionMap,
    PlaceholderedPayload, RestoreResult, Response,
)


def test_request_item_defaults():
    item = RequestItem(id="r1", task_type="summarize", payload="hello")
    assert item.sensitivity_hint is None
    assert item.requested_provider is None


def test_redaction_map_reuse_lookup():
    m = RedactionMap()
    e = RedactionEntry(placeholder="⟦PERSON_1⟧", original_value="Jane",
                       entity_type="PERSON", span=(0, 4), source="detector")
    m.entries.append(e)
    assert m.placeholder_for("Jane") == "⟦PERSON_1⟧"
    assert m.placeholder_for("Nobody") is None


def test_placeholdered_payload_summary_is_counts_only():
    p = PlaceholderedPayload(request_id="r1", text="⟦PERSON_1⟧",
                             entity_summary={"PERSON": 1})
    # entity_summary must never carry original values — only type→count
    assert p.entity_summary == {"PERSON": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.types'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/types.py
from dataclasses import dataclass, field
from typing import Literal, Optional

Route = Literal["local", "escalate"]
RedactionSource = Literal["detector", "user-added", "user-edited"]


@dataclass
class RequestItem:
    id: str
    task_type: str
    payload: str  # raw — Zone 1 only, never crosses the boundary
    sensitivity_hint: Optional[str] = None  # "low" | "high" | None
    requested_provider: Optional[str] = None


@dataclass
class RoutingDecision:
    request_id: str
    route: Route
    confidence: float
    classifier_version: str


@dataclass
class Entity:
    entity_type: str
    start: int
    end: int
    text: str


@dataclass
class RedactionEntry:
    placeholder: str
    original_value: str  # raw — Zone 1 only
    entity_type: str
    span: tuple[int, int]
    source: RedactionSource


@dataclass
class RedactionMap:
    entries: list[RedactionEntry] = field(default_factory=list)

    def placeholder_for(self, original_value: str) -> Optional[str]:
        for e in self.entries:
            if e.original_value == original_value:
                return e.placeholder
        return None


@dataclass
class PlaceholderedPayload:
    request_id: str
    text: str  # placeholdered — safe to cross the boundary
    entity_summary: dict[str, int]  # type -> count, never values


@dataclass
class RestoreResult:
    text: str
    restoration_anomalies: list[str]  # placeholders that could not be restored


@dataclass
class Response:
    request_id: str
    route: Route
    text: str
    restoration_anomalies: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_types.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/__init__.py cmndr/types.py tests/cmndr/__init__.py tests/cmndr/test_types.py
git commit -m "feat(cmndr): data model for device pipeline"
```

---

### Task 2: Backend interface + fake (`cmndr/backends/`)

**Files:**
- Create: `cmndr/backends/__init__.py` (empty)
- Create: `cmndr/backends/base.py`
- Create: `cmndr/backends/fake.py`
- Test: `tests/cmndr/test_backends.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Completion(text: str, tokens_generated: int)`; `Backend` Protocol with `infer(self, prompt: str, max_tokens: int = 512) -> Completion`; `EchoBackend` (test/dev impl).

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_backends.py
from cmndr.backends.base import Backend, Completion
from cmndr.backends.fake import EchoBackend


def test_echo_backend_returns_completion():
    backend: Backend = EchoBackend()
    out = backend.infer("hello world", max_tokens=10)
    assert isinstance(out, Completion)
    assert "hello world" in out.text
    assert out.tokens_generated == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_backends.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.backends'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/backends/base.py
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Completion:
    text: str
    tokens_generated: int


class Backend(Protocol):
    """Runs local inference for `local`-routed requests. No backend-specific
    type leaks upward (TR-9)."""

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion: ...
```

```python
# cmndr/backends/fake.py
from cmndr.backends.base import Completion


class EchoBackend:
    """Dev/test backend. Echoes the prompt as a stand-in for local inference."""

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion:
        return Completion(text=f"[local] {prompt}", tokens_generated=len(prompt.split()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_backends.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/backends/ tests/cmndr/test_backends.py
git commit -m "feat(cmndr): Backend interface + EchoBackend"
```

---

### Task 3: Provider interface + fakes (`cmndr/providers/`)

**Files:**
- Create: `cmndr/providers/__init__.py` (empty)
- Create: `cmndr/providers/base.py`
- Create: `cmndr/providers/fake.py`
- Test: `tests/cmndr/test_providers.py`

**Interfaces:**
- Consumes: `PlaceholderedPayload` from Task 1.
- Produces: `Provider` Protocol with `infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str` (returns placeholdered completion text); `EchoProvider` (returns text that still contains the placeholders); `SpyProvider` (records every payload it received, in `.received: list[PlaceholderedPayload]`).

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_providers.py
from cmndr.types import PlaceholderedPayload
from cmndr.providers.base import Provider
from cmndr.providers.fake import EchoProvider, SpyProvider


def _payload():
    return PlaceholderedPayload(request_id="r1", text="Summarize ⟦ORG_1⟧",
                               entity_summary={"ORG": 1})


def test_echo_provider_preserves_placeholders():
    provider: Provider = EchoProvider()
    out = provider.infer(_payload())
    assert "⟦ORG_1⟧" in out


def test_spy_provider_records_payloads():
    spy = SpyProvider()
    spy.infer(_payload())
    assert len(spy.received) == 1
    assert spy.received[0].request_id == "r1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.providers'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/providers/base.py
from typing import Protocol
from cmndr.types import PlaceholderedPayload


class Provider(Protocol):
    """The escalation target. Receives ONLY placeholdered payloads — never raw
    text or the RedactionMap. The GPU network slots in here later (V7/§5.3)."""

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str: ...
```

```python
# cmndr/providers/fake.py
from cmndr.types import PlaceholderedPayload


class EchoProvider:
    """Returns a completion that still contains the placeholders, so restore works."""

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str:
        return f"Summary: {payload.text}"


class SpyProvider:
    """Records every payload it was handed — used to assert the boundary invariant."""

    def __init__(self) -> None:
        self.received: list[PlaceholderedPayload] = []

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str:
        self.received.append(payload)
        return f"Summary: {payload.text}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_providers.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/providers/ tests/cmndr/test_providers.py
git commit -m "feat(cmndr): Provider interface + Echo/Spy fakes"
```

---

### Task 4: Detector seam (`cmndr/detect.py`)

**Files:**
- Create: `cmndr/detect.py`
- Test: `tests/cmndr/test_detect.py`

**Interfaces:**
- Consumes: `Entity` from Task 1.
- Produces: `Detector` Protocol with `detect(self, text: str) -> list[Entity]`; `WordlistDetector(mapping: dict[str, str])` (offline test detector mapping exact substrings → entity type). The real `PresidioDetector` is added in Plan 2; this task ships the seam + offline detector so the sandwich is testable without spaCy.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_detect.py
from cmndr.detect import Detector, WordlistDetector


def test_wordlist_detector_finds_all_occurrences():
    det: Detector = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    ents = det.detect("Jane Smith met Acme Corp and Jane Smith left")
    kinds = sorted((e.entity_type, e.text) for e in ents)
    assert ("ORG", "Acme Corp") in kinds
    assert sum(1 for e in ents if e.text == "Jane Smith") == 2
    # spans must be correct and non-overlapping
    for e in ents:
        assert "Jane Smith met Acme Corp and Jane Smith left"[e.start:e.end] == e.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.detect'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/detect.py
import re
from typing import Protocol
from cmndr.types import Entity


class Detector(Protocol):
    """detect(text) -> entities. Stronger detectors (Presidio) drop in later
    behind this same interface (§6.3, the V8 path)."""

    def detect(self, text: str) -> list[Entity]: ...


class WordlistDetector:
    """Offline, dependency-free detector for tests and demos: finds every exact
    occurrence of each known phrase."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def detect(self, text: str) -> list[Entity]:
        ents: list[Entity] = []
        for phrase, etype in self._mapping.items():
            for m in re.finditer(re.escape(phrase), text):
                ents.append(Entity(entity_type=etype, start=m.start(),
                                   end=m.end(), text=phrase))
        ents.sort(key=lambda e: e.start)
        return ents
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_detect.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/detect.py tests/cmndr/test_detect.py
git commit -m "feat(cmndr): Detector seam + offline WordlistDetector"
```

---

### Task 5: Anonymizer (`cmndr/anonymizer.py`)

**Files:**
- Create: `cmndr/anonymizer.py`
- Test: `tests/cmndr/test_anonymizer.py`

**Interfaces:**
- Consumes: `Detector` (Task 4); `Entity`, `RedactionEntry`, `RedactionMap`, `PlaceholderedPayload` (Task 1).
- Produces: `Anonymizer(detector: Detector)` with `anonymize(self, request_id: str, text: str) -> tuple[PlaceholderedPayload, RedactionMap]`. Placeholder format `⟦TYPE_N⟧`, 1-indexed per type, identical values reuse the same placeholder (TR-4). All map entries get `source="detector"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_anonymizer.py
from cmndr.detect import WordlistDetector
from cmndr.anonymizer import Anonymizer


def _anon():
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    return Anonymizer(det)


def test_anonymize_replaces_and_summarizes():
    payload, rmap = _anon().anonymize("r1", "Jane Smith joined Acme Corp")
    assert payload.text == "⟦PERSON_1⟧ joined ⟦ORG_1⟧"
    assert payload.entity_summary == {"PERSON": 1, "ORG": 1}
    assert payload.request_id == "r1"


def test_identical_values_reuse_placeholder():
    payload, rmap = _anon().anonymize("r1", "Jane Smith and Jane Smith")
    assert payload.text == "⟦PERSON_1⟧ and ⟦PERSON_1⟧"
    assert payload.entity_summary == {"PERSON": 1}
    assert all(e.source == "detector" for e in rmap.entries)
    assert len(rmap.entries) == 1


def test_no_raw_value_in_payload_or_summary():
    payload, rmap = _anon().anonymize("r1", "Jane Smith joined Acme Corp")
    assert "Jane Smith" not in payload.text
    assert "Acme Corp" not in str(payload.entity_summary)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_anonymizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.anonymizer'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/anonymizer.py
from cmndr.detect import Detector
from cmndr.types import RedactionEntry, RedactionMap, PlaceholderedPayload


class Anonymizer:
    """Detect → placeholder. Holds a reversible RedactionMap on-device for the
    request lifetime. The map is never serialized across the boundary."""

    def __init__(self, detector: Detector) -> None:
        self._detector = detector

    def anonymize(self, request_id: str, text: str) -> tuple[PlaceholderedPayload, RedactionMap]:
        entities = self._detector.detect(text)
        # Replace right-to-left so spans don't shift.
        entities = sorted(entities, key=lambda e: e.start, reverse=True)

        rmap = RedactionMap()
        type_counters: dict[str, int] = {}
        out = text

        for ent in entities:
            existing = rmap.placeholder_for(ent.text)
            if existing is not None:
                placeholder = existing
            else:
                type_counters[ent.entity_type] = type_counters.get(ent.entity_type, 0) + 1
                placeholder = f"⟦{ent.entity_type}_{type_counters[ent.entity_type]}⟧"
                rmap.entries.append(RedactionEntry(
                    placeholder=placeholder, original_value=ent.text,
                    entity_type=ent.entity_type, span=(ent.start, ent.end),
                    source="detector",
                ))
            out = out[:ent.start] + placeholder + out[ent.end:]

        summary: dict[str, int] = {}
        for e in rmap.entries:
            summary[e.entity_type] = summary.get(e.entity_type, 0) + 1

        return PlaceholderedPayload(request_id=request_id, text=out,
                                    entity_summary=summary), rmap
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_anonymizer.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/anonymizer.py tests/cmndr/test_anonymizer.py
git commit -m "feat(cmndr): Anonymizer with reversible RedactionMap (TR-4)"
```

---

### Task 6: Restorer (`cmndr/restore.py`)

**Files:**
- Create: `cmndr/restore.py`
- Test: `tests/cmndr/test_restore.py`

**Interfaces:**
- Consumes: `RedactionMap`, `RestoreResult` (Task 1).
- Produces: `restore(text: str, redaction_map: RedactionMap) -> RestoreResult`. Every placeholder present is replaced with its original value; any map placeholder NOT found in `text` is appended to `restoration_anomalies` (never silently lost — TR-3, §6.5).

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_restore.py
from cmndr.types import RedactionEntry, RedactionMap
from cmndr.restore import restore


def _map():
    m = RedactionMap()
    m.entries.append(RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON", (0, 10), "detector"))
    m.entries.append(RedactionEntry("⟦ORG_1⟧", "Acme Corp", "ORG", (20, 29), "detector"))
    return m


def test_restore_substitutes_all_placeholders():
    res = restore("Summary: ⟦PERSON_1⟧ at ⟦ORG_1⟧", _map())
    assert res.text == "Summary: Jane Smith at Acme Corp"
    assert res.restoration_anomalies == []


def test_dropped_placeholder_is_flagged_not_lost():
    # Model paraphrased away ⟦ORG_1⟧
    res = restore("Summary about ⟦PERSON_1⟧ only", _map())
    assert "Jane Smith" in res.text
    assert res.restoration_anomalies == ["⟦ORG_1⟧"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_restore.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.restore'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/restore.py
from cmndr.types import RedactionMap, RestoreResult


def restore(text: str, redaction_map: RedactionMap) -> RestoreResult:
    out = text
    anomalies: list[str] = []
    for entry in redaction_map.entries:
        if entry.placeholder in out:
            out = out.replace(entry.placeholder, entry.original_value)
        else:
            anomalies.append(entry.placeholder)
    return RestoreResult(text=out, restoration_anomalies=anomalies)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_restore.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/restore.py tests/cmndr/test_restore.py
git commit -m "feat(cmndr): Restorer with anomaly flagging (TR-3)"
```

---

### Task 7: Router (`cmndr/router.py`)

**Files:**
- Create: `cmndr/router.py`
- Test: `tests/cmndr/test_router.py`

**Interfaces:**
- Consumes: `RequestItem`, `RoutingDecision` (Task 1).
- Produces: `Router` Protocol with `classify(self, item: RequestItem) -> RoutingDecision`; `ThresholdRouter(threshold: float = 0.7, version: str = "stub-v0")`. MVP stub heuristic (real classifier is Plan 2): `sensitivity_hint == "high"` → escalate (confidence below threshold); otherwise local (confidence 0.9). Erring toward escalate is safe because the sandwich protects escalated traffic (§6.2). `threshold` is the calibration knob, configurable, not hardcoded at call sites.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_router.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.router'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/router.py
from typing import Protocol
from cmndr.types import RequestItem, RoutingDecision


class Router(Protocol):
    def classify(self, item: RequestItem) -> RoutingDecision: ...


class ThresholdRouter:
    """MVP stub: routes on the sensitivity hint. Replaced by a trained local
    classifier in Plan 2; the threshold stays the calibration knob (§6.2)."""

    def __init__(self, threshold: float = 0.7, version: str = "stub-v0") -> None:
        self._threshold = threshold
        self._version = version

    def classify(self, item: RequestItem) -> RoutingDecision:
        if item.sensitivity_hint == "high":
            return RoutingDecision(item.id, "escalate", confidence=0.4,
                                   classifier_version=self._version)
        return RoutingDecision(item.id, "local", confidence=0.9,
                               classifier_version=self._version)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_router.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/router.py tests/cmndr/test_router.py
git commit -m "feat(cmndr): ThresholdRouter stub (TR-1)"
```

---

### Task 8: Pre-flight preview (`cmndr/preview.py`)

**Files:**
- Create: `cmndr/preview.py`
- Test: `tests/cmndr/test_preview.py`

**Interfaces:**
- Consumes: `PlaceholderedPayload`, `RedactionMap`, `RedactionEntry` (Task 1).
- Produces: `EscalationNotAccepted(Exception)`; `Preview(payload: PlaceholderedPayload, redaction_map: RedactionMap)` with attributes `.accepted: bool` and methods `accept() -> None`, `remove_redaction(placeholder: str) -> None` (false positive: un-redacts, marks the entry `source="user-edited"` and removes it from the map + restores the value into `payload.text`). A `Preview` starts `accepted=False`. Escalation gating itself lives in Task 9 (the pipeline checks `preview.accepted`).

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_preview.py
from cmndr.types import PlaceholderedPayload, RedactionMap, RedactionEntry
from cmndr.preview import Preview


def _preview():
    rmap = RedactionMap()
    rmap.entries.append(RedactionEntry("⟦ORG_1⟧", "Acme Corp", "ORG", (3, 12), "detector"))
    payload = PlaceholderedPayload("r1", "at ⟦ORG_1⟧", {"ORG": 1})
    return Preview(payload, rmap)


def test_preview_starts_unaccepted():
    assert _preview().accepted is False


def test_accept_flips_flag():
    p = _preview()
    p.accept()
    assert p.accepted is True


def test_remove_redaction_restores_value_and_drops_entry():
    p = _preview()
    p.remove_redaction("⟦ORG_1⟧")
    assert p.payload.text == "at Acme Corp"
    assert all(e.placeholder != "⟦ORG_1⟧" for e in p.redaction_map.entries)
    assert p.payload.entity_summary.get("ORG", 0) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_preview.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.preview'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/preview.py
from cmndr.types import PlaceholderedPayload, RedactionMap


class EscalationNotAccepted(Exception):
    """Raised when escalation is attempted without an accepted preview (TR-6)."""


class Preview:
    """Surfaces the exact placeholdered payload before it can cross the boundary.
    The user may accept, or remove a wrong redaction (false positive). Escalation
    is structurally impossible until `accepted` is True (enforced in the pipeline)."""

    def __init__(self, payload: PlaceholderedPayload, redaction_map: RedactionMap) -> None:
        self.payload = payload
        self.redaction_map = redaction_map
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True

    def remove_redaction(self, placeholder: str) -> None:
        entry = next((e for e in self.redaction_map.entries
                      if e.placeholder == placeholder), None)
        if entry is None:
            return
        self.payload.text = self.payload.text.replace(placeholder, entry.original_value)
        self.redaction_map.entries.remove(entry)
        remaining = sum(1 for e in self.redaction_map.entries
                        if e.entity_type == entry.entity_type)
        if remaining:
            self.payload.entity_summary[entry.entity_type] = remaining
        else:
            self.payload.entity_summary.pop(entry.entity_type, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_preview.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/preview.py tests/cmndr/test_preview.py
git commit -m "feat(cmndr): pre-flight Preview with accept + false-positive removal (M3)"
```

---

### Task 9: Pipeline / Entry Point (`cmndr/pipeline.py`)

**Files:**
- Create: `cmndr/pipeline.py`
- Test: `tests/cmndr/test_pipeline.py`

**Interfaces:**
- Consumes: `Router` (Task 7), `Anonymizer` (Task 5), `Backend` (Task 2), `Provider` (Task 3), `Preview`/`EscalationNotAccepted` (Task 8), `restore` (Task 6), `RequestItem`/`Response` (Task 1).
- Produces: `Pipeline(router, anonymizer, local_backend, provider)` with `process_item(self, item: RequestItem, approve: Optional[Callable[[Preview], None]] = None) -> Response` and `process_batch(self, items: list[RequestItem], approve=None) -> list[Response]`. Local route → local backend. Escalate route → anonymize → build `Preview` → call `approve(preview)` if given → if not `preview.accepted` raise `EscalationNotAccepted` → hand ONLY `preview.payload` to the provider → restore on return. The provider is never passed `item.payload` or the `RedactionMap`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cmndr/test_pipeline.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cmndr/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmndr.pipeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# cmndr/pipeline.py
from typing import Callable, Optional
from cmndr.types import RequestItem, Response
from cmndr.router import Router
from cmndr.anonymizer import Anonymizer
from cmndr.backends.base import Backend
from cmndr.providers.base import Provider
from cmndr.preview import Preview, EscalationNotAccepted
from cmndr.restore import restore


class Pipeline:
    """Entry Point (M7). Wires route → [local] | [anonymize → preview → provider
    → restore]. Only `preview.payload` ever reaches the provider — the boundary
    invariant is structural, not conventional."""

    def __init__(self, router: Router, anonymizer: Anonymizer,
                 local_backend: Backend, provider: Provider) -> None:
        self._router = router
        self._anonymizer = anonymizer
        self._local = local_backend
        self._provider = provider

    def process_item(self, item: RequestItem,
                     approve: Optional[Callable[[Preview], None]] = None) -> Response:
        decision = self._router.classify(item)

        if decision.route == "local":
            completion = self._local.infer(item.payload)
            return Response(item.id, "local", completion.text)

        payload, rmap = self._anonymizer.anonymize(item.id, item.payload)
        preview = Preview(payload, rmap)
        if approve is not None:
            approve(preview)
        if not preview.accepted:
            raise EscalationNotAccepted(item.id)

        completion = self._provider.infer(preview.payload)  # ONLY placeholdered text
        restored = restore(completion, preview.redaction_map)
        return Response(item.id, "escalate", restored.text,
                        restoration_anomalies=restored.restoration_anomalies)

    def process_batch(self, items: list[RequestItem],
                      approve: Optional[Callable[[Preview], None]] = None) -> list[Response]:
        return [self.process_item(it, approve=approve) for it in items]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cmndr/test_pipeline.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add cmndr/pipeline.py tests/cmndr/test_pipeline.py
git commit -m "feat(cmndr): Pipeline entry point with structural boundary invariant (TR-6/7)"
```

---

### Task 10: Full-suite green + smoke run

**Files:**
- Create: `cmndr/demo.py`
- Test: (runs the whole suite)

**Interfaces:**
- Consumes: everything above.
- Produces: `cmndr/demo.py` — a `__main__` that builds a `Pipeline` with the fakes and runs one local + one escalate item, printing the restored output. Documents how the pieces wire together for the next plan.

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests/cmndr/ -v`
Expected: PASS (all tests from Tasks 1–9 green)

- [ ] **Step 2: Write the demo**

```python
# cmndr/demo.py
from cmndr.types import RequestItem
from cmndr.router import ThresholdRouter
from cmndr.anonymizer import Anonymizer
from cmndr.detect import WordlistDetector
from cmndr.backends.fake import EchoBackend
from cmndr.providers.fake import EchoProvider
from cmndr.pipeline import Pipeline


def build() -> Pipeline:
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    return Pipeline(ThresholdRouter(), Anonymizer(det), EchoBackend(), EchoProvider())


if __name__ == "__main__":
    pipe = build()
    local = pipe.process_item(
        RequestItem("d1", "summarize", "what is 2+2", sensitivity_hint="low"))
    print("LOCAL :", local.route, "->", local.text)
    esc = pipe.process_item(
        RequestItem("d2", "summarize", "Jane Smith joined Acme Corp",
                    sensitivity_hint="high"),
        approve=lambda p: p.accept())
    print("ESCAL :", esc.route, "->", esc.text)
```

- [ ] **Step 3: Run the demo**

Run: `python -m cmndr.demo`
Expected output:
```
LOCAL : local -> [local] what is 2+2
ESCAL : escalate -> Summary: Jane Smith joined Acme Corp
```

- [ ] **Step 4: Commit**

```bash
git add cmndr/demo.py
git commit -m "chore(cmndr): demo wiring + full device-pipeline suite green"
```

---

## Self-Review

**1. Spec coverage (this slice = §13 phases 1–3):**
- Entry Point (M7) → Task 9. Backend interface + local backend (TR-9) → Tasks 2, 9. Data model (§10) → Task 1. ✅ phase 1
- Anonymizer + map + Restorer + Provider interface (TR-3/4/7) → Tasks 3, 5, 6. ✅ phase 2
- Preview + no-unreviewed-escalation invariant (TR-6) → Tasks 8, 9 (`test_escalate_without_accept_is_blocked`). ✅ phase 3
- Boundary invariant (TR-7) → Task 9 (`test_boundary_invariant_provider_never_sees_raw_pii`). ✅
- Router (TR-1) → Task 7 (stub; real classifier + TR-2 calibration deferred to Plan 2, as designed). Audit (TR-8), real Presidio detector, and the network (TR-12+) are explicitly later plans.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code. ✅

**3. Type consistency:** `infer(prompt, max_tokens)` / `infer(payload, max_tokens)` consistent across Tasks 2/3/9. `anonymize(request_id, text) -> (PlaceholderedPayload, RedactionMap)` matches its use in Task 9. `restore(text, redaction_map) -> RestoreResult` matches. `Preview(payload, redaction_map)` / `.accept()` / `.accepted` consistent across Tasks 8/9. `RoutingDecision(request_id, route, confidence, classifier_version)` consistent. ✅
