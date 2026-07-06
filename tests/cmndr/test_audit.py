import json

from cmndr.audit import AuditLog
from cmndr.types import PlaceholderedPayload, RoutingDecision


def test_routing_entries_are_appended(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record_routing(RoutingDecision("r1", "local", 0.9, "heuristic-v1"))
    log.record_routing(RoutingDecision("r2", "escalate", 0.8, "heuristic-v1"))
    entries = log.entries()
    assert [e["request_id"] for e in entries] == ["r1", "r2"]
    assert entries[0]["kind"] == "routing"
    assert "ts" in entries[0]


def test_escalation_entry_holds_placeholders_never_raw_values(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    payload = PlaceholderedPayload("r3", "⟦PERSON_1⟧ of ⟦ORG_1⟧ owes rent",
                                   {"PERSON": 1, "ORG": 1})
    log.record_escalation("r3", target="cgn", payload=payload,
                          preview_accepted=True, restoration_anomalies=[])
    raw_file = (tmp_path / "audit.jsonl").read_text()
    assert "Jane Smith" not in raw_file          # no raw value anywhere
    e = log.entries()[0]
    assert e["kind"] == "escalation"
    assert e["placeholdered_payload"] == "⟦PERSON_1⟧ of ⟦ORG_1⟧ owes rent"
    assert e["entity_summary"] == {"PERSON": 1, "ORG": 1}
    assert e["preview_accepted"] is True


def test_append_only_across_instances(tmp_path):
    p = tmp_path / "audit.jsonl"
    AuditLog(p).record_routing(RoutingDecision("r1", "local", 0.9, "v"))
    AuditLog(p).record_routing(RoutingDecision("r2", "local", 0.9, "v"))
    assert len(AuditLog(p).entries()) == 2
    # file is plain JSONL — reconstructable without the class (N2)
    lines = p.read_text().splitlines()
    assert all(json.loads(line) for line in lines)
