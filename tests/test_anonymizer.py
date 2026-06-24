"""
Unit tests for the anonymizer pipeline — uses StubAnonymizer (no spaCy needed).
"""

import pytest
from anonymizer.pipeline import StubAnonymizer, make_anonymizer


def test_stub_anonymizer_passthrough():
    anon = StubAnonymizer()
    text = "Jane Smith from Acme Corp owes €42,000."
    anonymized, enc_map = anon.anonymize(text)
    assert anonymized == text  # stub doesn't change anything


def test_stub_restore_is_identity():
    anon = StubAnonymizer()
    text = "Some result with [PERSON_1] placeholder."
    _, enc_map = anon.anonymize("anything")
    restored = anon.restore(text, enc_map)
    assert restored == text  # empty map → no replacements


def test_make_anonymizer_returns_stub_when_deps_missing(monkeypatch):
    import anonymizer.pipeline as pipeline
    monkeypatch.setattr(pipeline, "_deps_available", False)
    anon = make_anonymizer()
    assert isinstance(anon, StubAnonymizer)


def test_make_anonymizer_stub_flag():
    anon = make_anonymizer(stub=True)
    assert isinstance(anon, StubAnonymizer)


def test_stub_encrypt_decrypt_roundtrip():
    anon = StubAnonymizer()
    text = "Hello world"
    anonymized, enc_map = anon.anonymize(text)
    restored = anon.restore(anonymized, enc_map)
    assert restored == anonymized
