from cmndr.detect import WordlistDetector
from cmndr.anonymizer import Anonymizer


def _anon():
    det = WordlistDetector({"Jane Smith": "PERSON", "Acme Corp": "ORG"})
    return Anonymizer(det)


def test_anonymize_replaces_and_summarizes():
    payload, rmap = _anon().anonymize("r1", "Jane Smith joined Acme Corp")
    assert payload.text == "⟦PERSON_1⟧ joined ⟦ORG_1⟧"
    assert payload.entity_summary == {"PERSON": 1, "ORG": 1}
    assert payload.request_id == "r1"


def test_identical_values_reuse_placeholder():
    payload, rmap = _anon().anonymize("r1", "Jane Smith and Jane Smith")
    assert payload.text == "⟦PERSON_1⟧ and ⟦PERSON_1⟧"
    assert payload.entity_summary == {"PERSON": 1}
    assert all(e.source == "detector" for e in rmap.entries)
    assert len(rmap.entries) == 1


def test_no_raw_value_in_payload_or_summary():
    payload, rmap = _anon().anonymize("r1", "Jane Smith joined Acme Corp")
    assert "Jane Smith" not in payload.text
    assert "Acme Corp" not in str(payload.entity_summary)
