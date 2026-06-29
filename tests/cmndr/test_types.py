from cmndr.types import (
    RequestItem, RoutingDecision, Entity, RedactionEntry, RedactionMap,
    PlaceholderedPayload, RestoreResult, Response,
)


def test_request_item_defaults():
    item = RequestItem(id="r1", task_type="summarize", payload="hello")
    assert item.sensitivity_hint is None
    assert item.requested_provider is None


def test_redaction_map_reuse_lookup():
    m = RedactionMap()
    e = RedactionEntry(placeholder="⟦PERSON_1⟧", original_value="Jane",
                       entity_type="PERSON", span=(0, 4), source="detector")
    m.entries.append(e)
    assert m.placeholder_for("Jane") == "⟦PERSON_1⟧"
    assert m.placeholder_for("Nobody") is None


def test_placeholdered_payload_summary_is_counts_only():
    p = PlaceholderedPayload(request_id="r1", text="⟦PERSON_1⟧",
                             entity_summary={"PERSON": 1})
    # entity_summary must never carry original values — only type→count
    assert p.entity_summary == {"PERSON": 1}
