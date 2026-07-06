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
