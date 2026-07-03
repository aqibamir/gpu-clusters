from cmndr.detect import Detector
from cmndr.types import RedactionEntry, RedactionMap, PlaceholderedPayload


class Anonymizer:
    """Detect → placeholder. Holds a reversible RedactionMap on-device for the
    request lifetime. The map is never serialized across the boundary."""

    def __init__(self, detector: Detector) -> None:
        self._detector = detector

    def anonymize(self, request_id: str, text: str) -> tuple[PlaceholderedPayload, RedactionMap]:
        entities = self._detector.detect(text)

        rmap = RedactionMap()
        type_counters: dict[str, int] = {}

        # Pass 1: left-to-right by start, assign placeholders per unique
        # original value so numbering follows textual (first-occurrence) order.
        for ent in sorted(entities, key=lambda e: e.start):
            if rmap.placeholder_for(ent.text) is not None:
                continue
            type_counters[ent.entity_type] = type_counters.get(ent.entity_type, 0) + 1
            placeholder = f"⟦{ent.entity_type}_{type_counters[ent.entity_type]}⟧"
            rmap.entries.append(RedactionEntry(
                placeholder=placeholder, original_value=ent.text,
                entity_type=ent.entity_type, span=(ent.start, ent.end),
                source="detector",
            ))

        # Pass 2: right-to-left substitution using the already-built map so
        # spans don't shift as replacements happen.
        out = text
        for ent in sorted(entities, key=lambda e: e.start, reverse=True):
            placeholder = rmap.placeholder_for(ent.text)
            out = out[:ent.start] + placeholder + out[ent.end:]

        summary: dict[str, int] = {}
        for e in rmap.entries:
            summary[e.entity_type] = summary.get(e.entity_type, 0) + 1

        return PlaceholderedPayload(request_id=request_id, text=out,
                                    entity_summary=summary), rmap
