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
