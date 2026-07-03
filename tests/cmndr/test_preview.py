# tests/cmndr/test_preview.py
from cmndr.types import PlaceholderedPayload, RedactionMap, RedactionEntry
from cmndr.preview import Preview


def _preview():
    rmap = RedactionMap()
    rmap.entries.append(RedactionEntry("⟦ORG_1⟧", "Acme Corp", "ORG", (3, 12), "detector"))
    payload = PlaceholderedPayload("r1", "at ⟦ORG_1⟧", {"ORG": 1})
    return Preview(payload, rmap)


def test_preview_starts_unaccepted():
    assert _preview().accepted is False


def test_accept_flips_flag():
    p = _preview()
    p.accept()
    assert p.accepted is True


def test_remove_redaction_restores_value_and_drops_entry():
    p = _preview()
    p.remove_redaction("⟦ORG_1⟧")
    assert p.payload.text == "at Acme Corp"
    assert all(e.placeholder != "⟦ORG_1⟧" for e in p.redaction_map.entries)
    assert p.payload.entity_summary.get("ORG", 0) == 0
