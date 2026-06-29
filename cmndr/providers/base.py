from typing import Protocol
from cmndr.types import PlaceholderedPayload


class Provider(Protocol):
    """The escalation target. Receives ONLY placeholdered payloads — never raw
    text or the RedactionMap. The GPU network slots in here later (V7/§5.3)."""

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str: ...
