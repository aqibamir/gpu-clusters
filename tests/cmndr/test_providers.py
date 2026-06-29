from cmndr.types import PlaceholderedPayload
from cmndr.providers.base import Provider
from cmndr.providers.fake import EchoProvider, SpyProvider


def _payload():
    return PlaceholderedPayload(request_id="r1", text="Summarize ⟦ORG_1⟧",
                               entity_summary={"ORG": 1})


def test_echo_provider_preserves_placeholders():
    provider: Provider = EchoProvider()
    out = provider.infer(_payload())
    assert "⟦ORG_1⟧" in out


def test_spy_provider_records_payloads():
    spy = SpyProvider()
    spy.infer(_payload())
    assert len(spy.received) == 1
    assert spy.received[0].request_id == "r1"
