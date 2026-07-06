"""TR-10: on-device challenge — confirm outbound traffic for a request contains
none of its original entity values. Runs after preview-accept, before dispatch.
Structural backstop for the boundary invariant (TR-7)."""

from cmndr.types import RedactionMap


class LeakageDetected(Exception):
    def __init__(self, leaked: list[str]) -> None:
        self.leaked = leaked
        super().__init__(f"raw entity values in outbound payload: {leaked}")


def verify_no_leakage(outbound_text: str, redaction_map: RedactionMap) -> list[str]:
    return [e.original_value for e in redaction_map.entries
            if e.original_value in outbound_text]


def assert_no_leakage(outbound_text: str, redaction_map: RedactionMap) -> None:
    leaked = verify_no_leakage(outbound_text, redaction_map)
    if leaked:
        raise LeakageDetected(leaked)
