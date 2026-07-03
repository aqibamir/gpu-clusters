from cmndr.types import RedactionEntry, RedactionMap
from cmndr.restore import restore


def _map():
    m = RedactionMap()
    m.entries.append(RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON", (0, 10), "detector"))
    m.entries.append(RedactionEntry("⟦ORG_1⟧", "Acme Corp", "ORG", (20, 29), "detector"))
    return m


def test_restore_substitutes_all_placeholders():
    res = restore("Summary: ⟦PERSON_1⟧ at ⟦ORG_1⟧", _map())
    assert res.text == "Summary: Jane Smith at Acme Corp"
    assert res.restoration_anomalies == []


def test_dropped_placeholder_is_flagged_not_lost():
    # Model paraphrased away ⟦ORG_1⟧
    res = restore("Summary about ⟦PERSON_1⟧ only", _map())
    assert "Jane Smith" in res.text
    assert res.restoration_anomalies == ["⟦ORG_1⟧"]
