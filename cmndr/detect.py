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
