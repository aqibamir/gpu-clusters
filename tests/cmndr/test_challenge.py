import pytest

from cmndr.challenge import LeakageDetected, assert_no_leakage, verify_no_leakage
from cmndr.types import RedactionEntry, RedactionMap


def rmap() -> RedactionMap:
    m = RedactionMap()
    m.entries.append(RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON", (0, 10), "detector"))
    m.entries.append(RedactionEntry("⟦ORG_1⟧", "Acme Corp", "ORG", (14, 23), "detector"))
    return m


def test_clean_payload_passes():
    assert verify_no_leakage("⟦PERSON_1⟧ of ⟦ORG_1⟧ owes rent", rmap()) == []


def test_corrupted_payload_fails_and_names_the_leak():
    leaked = verify_no_leakage("Jane Smith of ⟦ORG_1⟧ owes rent", rmap())
    assert leaked == ["Jane Smith"]


def test_assert_raises_with_leak_list():
    with pytest.raises(LeakageDetected) as exc:
        assert_no_leakage("Jane Smith of Acme Corp", rmap())
    assert set(exc.value.leaked) == {"Jane Smith", "Acme Corp"}


def test_user_removed_redactions_are_not_leaks():
    # a value the user deliberately un-redacted is no longer in the map,
    # so its presence is not flagged — the map is the source of truth
    m = rmap()
    m.entries = [e for e in m.entries if e.placeholder != "⟦ORG_1⟧"]
    assert verify_no_leakage("⟦PERSON_1⟧ of Acme Corp owes rent", m) == []
