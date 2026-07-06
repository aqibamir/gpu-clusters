import pytest

from cmndr.detectors.presidio import PresidioDetector, spacy_model_available

MODEL = "en_core_web_sm"
needs_model = pytest.mark.skipif(
    not spacy_model_available(MODEL),
    reason=f"spaCy model {MODEL} not installed",
)


@needs_model
def test_detects_person_and_email():
    text = "Contact Jane Smith at jane.smith@acme.com please"
    det = PresidioDetector(model=MODEL)
    ents = det.detect(text)
    types = {e.entity_type for e in ents}
    assert "PERSON" in types
    assert "EMAIL_ADDRESS" in types
    # spans must slice back to the reported text
    for e in ents:
        assert text[e.start:e.end] == e.text


@needs_model
def test_normalizes_organization_to_org():
    det = PresidioDetector(model=MODEL)
    ents = det.detect("Acme Corporation signed the deal.")
    assert "ORG" in {e.entity_type for e in ents}


@needs_model
def test_entities_sorted_by_start():
    det = PresidioDetector(model=MODEL)
    ents = det.detect("Email bob.jones@example.com about Bob Jones.")
    starts = [e.start for e in ents]
    assert starts == sorted(starts)


def test_model_available_helper_false_for_nonsense():
    assert spacy_model_available("no_such_model_xyz") is False
