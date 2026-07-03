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
