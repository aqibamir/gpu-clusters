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
