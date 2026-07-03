from typing import Protocol
from cmndr.types import RequestItem, RoutingDecision


class Router(Protocol):
    """classify(item) -> decision. Routes requests to local or escalate paths
    based on sensitivity and configured threshold; confidence reflects routing
    confidence and must stay below the configured threshold for escalates."""

    def classify(self, item: RequestItem) -> RoutingDecision: ...


class ThresholdRouter:
    """MVP stub: routes on the sensitivity hint. Replaced by a trained local
    classifier in Plan 2; the threshold stays the calibration knob (§6.2)."""

    def __init__(self, threshold: float = 0.7, version: str = "stub-v0") -> None:
        self._threshold = threshold
        self._version = version

    def classify(self, item: RequestItem) -> RoutingDecision:
        if item.sensitivity_hint == "high":
            return RoutingDecision(item.id, "escalate",
                                   confidence=max(0.0, self._threshold - 0.3),
                                   classifier_version=self._version)
        return RoutingDecision(item.id, "local", confidence=0.9,
                               classifier_version=self._version)
