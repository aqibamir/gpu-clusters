import pytest

from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.fake import EchoBackend
from cmndr.challenge import LeakageDetected
from cmndr.detect import WordlistDetector
from cmndr.pipeline import Pipeline
from cmndr.providers.fake import EchoProvider
from cmndr.router import ThresholdRouter
from cmndr.types import RedactionEntry, RequestItem


def make_pipeline(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    pipe = Pipeline(
        router=ThresholdRouter(),
        anonymizer=Anonymizer(WordlistDetector({"Jane Smith": "PERSON"})),
        local_backend=EchoBackend(),
        provider=EchoProvider(),
        audit=audit,
    )
    return pipe, audit


def test_local_route_writes_routing_entry(tmp_path):
    pipe, audit = make_pipeline(tmp_path)
    pipe.process_item(RequestItem("r1", "summarize", "what is 2+2", sensitivity_hint="low"))
    kinds = [e["kind"] for e in audit.entries()]
    assert kinds == ["routing"]


def test_escalation_writes_routing_and_escalation_entries(tmp_path):
    pipe, audit = make_pipeline(tmp_path)
    pipe.process_item(
        RequestItem("r2", "summarize", "Jane Smith owes rent", sensitivity_hint="high"),
        approve=lambda p: p.accept())
    entries = audit.entries()
    assert [e["kind"] for e in entries] == ["routing", "escalation"]
    esc = entries[1]
    assert "Jane Smith" not in esc["placeholdered_payload"]
    assert esc["preview_accepted"] is True


def test_leaking_preview_edit_is_blocked_before_provider(tmp_path):
    pipe, audit = make_pipeline(tmp_path)

    def approve_and_unredact(p):
        p.remove_redaction("⟦PERSON_1⟧")  # user removes the redaction...
        p.redaction_map.entries.append(     # ...but the map says it must be hidden
            RedactionEntry("⟦PERSON_1⟧", "Jane Smith", "PERSON", (0, 10), "detector"))
        p.accept()

    with pytest.raises(LeakageDetected):
        pipe.process_item(
            RequestItem("r3", "summarize", "Jane Smith owes rent", sensitivity_hint="high"),
            approve=approve_and_unredact)


def test_audit_is_optional_default_none(tmp_path):
    pipe = Pipeline(ThresholdRouter(),
                    Anonymizer(WordlistDetector({})),
                    EchoBackend(), EchoProvider())
    out = pipe.process_item(RequestItem("r4", "summarize", "hi", sensitivity_hint="low"))
    assert out.route == "local"
