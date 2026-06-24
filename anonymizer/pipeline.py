"""
Anonymization pipeline — runs entirely on the SME device.

The restoration map is Fernet-encrypted and never leaves the device.
spaCy + Presidio detect entities; we replace them with typed placeholders.
"""

import json
import re
from typing import Optional

from cryptography.fernet import Fernet

# Deferred imports so the node_agent can import without these deps installed.
try:
    import spacy
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import SpacyNlpEngine
    _deps_available = True
except ImportError:
    _deps_available = False


# Entity types we anonymize, in priority order.
# Presidio types: https://microsoft.github.io/presidio/supported_entities/
_ENTITY_TYPES = [
    "PERSON",
    "ORG",
    "LOCATION",
    "DATE_TIME",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "IBAN_CODE",
    "CREDIT_CARD",
    "US_SSN",
    "MONEY",
    "NRP",          # Nationality / religious / political group
]


class Anonymizer:
    """
    Anonymizes text and produces an encrypted restoration map.

    Usage:
        anon = Anonymizer()
        anonymized, enc_map = anon.anonymize("Jane Smith from Acme Corp")
        # → "[PERSON_1] from [ORG_1]", <encrypted bytes>

        restored = anon.restore(result_text, enc_map)
        # → original entity values substituted back in
    """

    def __init__(self, model: str = "en_core_web_lg"):
        if not _deps_available:
            raise RuntimeError(
                "spacy and presidio-analyzer are required. "
                "Install with: uv pip install spacy presidio-analyzer presidio-anonymizer "
                "&& python -m spacy download en_core_web_lg"
            )
        nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": model}])
        self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        self._fernet_key = Fernet.generate_key()
        self._fernet = Fernet(self._fernet_key)

    def anonymize(self, text: str) -> tuple[str, bytes]:
        """
        Returns (anonymized_text, encrypted_restoration_map).

        A fresh Fernet key is generated per Anonymizer instance.
        The encrypted map must be kept locally to restore results.
        """
        results = self._analyzer.analyze(
            text=text,
            entities=_ENTITY_TYPES,
            language="en",
        )

        # Sort by start position descending so replacements don't shift offsets.
        results = sorted(results, key=lambda r: r.start, reverse=True)

        restoration_map: dict[str, str] = {}
        type_counters: dict[str, int] = {}
        anonymized = text

        for result in results:
            entity_type = result.entity_type
            original = text[result.start:result.end]

            # Reuse placeholder if we've already seen this exact string.
            existing = next(
                (k for k, v in restoration_map.items() if v == original), None
            )
            if existing:
                placeholder = existing
            else:
                type_counters[entity_type] = type_counters.get(entity_type, 0) + 1
                placeholder = f"[{entity_type}_{type_counters[entity_type]}]"
                restoration_map[placeholder] = original

            anonymized = anonymized[:result.start] + placeholder + anonymized[result.end:]

        encrypted_map = self._fernet.encrypt(json.dumps(restoration_map).encode())
        return anonymized, encrypted_map

    def restore(self, text: str, encrypted_map: bytes) -> str:
        """Replace placeholders in `text` with their original values."""
        restoration_map: dict[str, str] = json.loads(
            self._fernet.decrypt(encrypted_map).decode()
        )
        result = text
        for placeholder, original in restoration_map.items():
            result = result.replace(placeholder, original)
        return result


class StubAnonymizer:
    """
    No-op anonymizer for testing without spaCy/Presidio installed.
    Passes text through unchanged; encrypted map contains an empty dict.
    """

    def __init__(self):
        self._key = Fernet.generate_key()
        self._fernet = Fernet(self._key)

    def anonymize(self, text: str) -> tuple[str, bytes]:
        enc = self._fernet.encrypt(b"{}")
        return text, enc

    def restore(self, text: str, encrypted_map: bytes) -> str:
        return text


def make_anonymizer(stub: bool = False) -> "Anonymizer | StubAnonymizer":
    if stub or not _deps_available:
        return StubAnonymizer()
    return Anonymizer()
