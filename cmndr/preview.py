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
