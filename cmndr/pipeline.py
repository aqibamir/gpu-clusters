from typing import Callable, Optional
from cmndr.types import RequestItem, Response
from cmndr.router import Router
from cmndr.anonymizer import Anonymizer
from cmndr.audit import AuditLog
from cmndr.backends.base import Backend
from cmndr.challenge import assert_no_leakage
from cmndr.providers.base import Provider
from cmndr.preview import Preview, EscalationNotAccepted
from cmndr.restore import restore


class Pipeline:
    """Entry Point (M7). Wires route → [local] | [anonymize → preview → challenge
    → provider → restore]. Only `preview.payload` ever reaches the provider — the
    boundary invariant is structural, not conventional. Every decision is audited
    (TR-8); outbound text is challenge-verified after preview-accept (TR-10)."""

    def __init__(self, router: Router, anonymizer: Anonymizer,
                 local_backend: Backend, provider: Provider,
                 audit: Optional[AuditLog] = None) -> None:
        self._router = router
        self._anonymizer = anonymizer
        self._local = local_backend
        self._provider = provider
        self._audit = audit

    def process_item(self, item: RequestItem,
                     approve: Optional[Callable[[Preview], None]] = None) -> Response:
        decision = self._router.classify(item)
        if self._audit is not None:
            self._audit.record_routing(decision)

        if decision.route == "local":
            completion = self._local.infer(item.payload)
            return Response(item.id, "local", completion.text)

        payload, rmap = self._anonymizer.anonymize(item.id, item.payload)
        preview = Preview(payload, rmap)
        if approve is not None:
            approve(preview)
        if not preview.accepted:
            raise EscalationNotAccepted(item.id)

        assert_no_leakage(preview.payload.text, preview.redaction_map)  # TR-10

        completion = self._provider.infer(preview.payload)  # ONLY placeholdered text
        restored = restore(completion, preview.redaction_map)
        if self._audit is not None:
            self._audit.record_escalation(
                item.id, target="provider", payload=preview.payload,
                preview_accepted=True,
                restoration_anomalies=restored.restoration_anomalies)
        return Response(item.id, "escalate", restored.text,
                        restoration_anomalies=restored.restoration_anomalies)

    def process_batch(self, items: list[RequestItem],
                      approve: Optional[Callable[[Preview], None]] = None) -> list[Response]:
        return [self.process_item(it, approve=approve) for it in items]
