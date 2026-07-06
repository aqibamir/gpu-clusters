from cmndr.preview import Preview
from cmndr.types import PlaceholderedPayload, RedactionEntry, RedactionMap


def make_preview():
    m = RedactionMap()
    m.entries.append(RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON",
                                    (0, 10), "detector"))
    p = PlaceholderedPayload("r1", "⟦PERSON_1⟧ met Bob Jones at Initech",
                             {"PERSON": 1})
    return Preview(p, m)


def test_add_redaction_for_missed_entity():
    pv = make_preview()
    ph = pv.add_redaction("Bob Jones", "PERSON")
    assert ph == "⟦PERSON_2⟧"                      # next index for PERSON
    assert "Bob Jones" not in pv.payload.text
    assert pv.payload.entity_summary["PERSON"] == 2
    entry = pv.redaction_map.entries[-1]
    assert entry.source == "user-added"
    assert entry.original_value == "Bob Jones"


def test_add_redaction_new_type():
    pv = make_preview()
    assert pv.add_redaction("Initech", "ORG") == "⟦ORG_1⟧"
    assert pv.payload.entity_summary["ORG"] == 1


def test_add_redaction_absent_value_is_noop():
    pv = make_preview()
    assert pv.add_redaction("Nobody", "PERSON") is None
    assert pv.payload.entity_summary == {"PERSON": 1}
