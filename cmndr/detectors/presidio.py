"""Presidio-backed Detector (§6.3). Salvaged from anonymizer/pipeline.py but
implementing the cmndr Detector seam; returns Entity spans, never mutates text.

Presidio/spaCy emit some type names that differ from the cmndr taxonomy
(spaCy labels organizations ``ORGANIZATION``); we normalize to the taxonomy and
keep only allowed types, dropping noise like ``URL``."""

from functools import lru_cache

from cmndr.types import Entity

DEFAULT_ENTITY_TYPES = [
    "PERSON", "ORG", "LOCATION", "DATE_TIME", "PHONE_NUMBER",
    "EMAIL_ADDRESS", "IBAN_CODE", "CREDIT_CARD", "US_SSN", "MONEY", "NRP",
]

# Presidio/spaCy label -> cmndr taxonomy label.
_NORMALIZE = {
    "ORGANIZATION": "ORG",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "NORP": "NRP",
    "DATE": "DATE_TIME",
}


def spacy_model_available(model: str) -> bool:
    try:
        import spacy.util
        return spacy.util.is_package(model)
    except Exception:
        return False


@lru_cache(maxsize=2)
def _analyzer(model: str):
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import SpacyNlpEngine
    nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": model}])
    return AnalyzerEngine(nlp_engine=nlp_engine)


class PresidioDetector:
    def __init__(self, model: str = "en_core_web_sm",
                 entity_types: list[str] | None = None) -> None:
        self._model = model
        self._entity_types = set(entity_types or DEFAULT_ENTITY_TYPES)

    def detect(self, text: str) -> list[Entity]:
        # Let Presidio return everything, then normalize + filter to the
        # taxonomy — passing entities= drops spaCy types whose raw label
        # (e.g. ORGANIZATION) differs from the taxonomy label (ORG).
        results = _analyzer(self._model).analyze(text=text, language="en")
        ents: list[Entity] = []
        for r in results:
            etype = _NORMALIZE.get(r.entity_type, r.entity_type)
            if etype not in self._entity_types:
                continue
            ents.append(Entity(entity_type=etype, start=r.start, end=r.end,
                               text=text[r.start:r.end]))
        ents.sort(key=lambda e: e.start)
        return ents
