from cmndr.types import PlaceholderedPayload


class EchoProvider:
    """Returns a completion that still contains the placeholders, so restore works."""

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str:
        return f"Summary: {payload.text}"


class SpyProvider:
    """Records every payload it was handed — used to assert the boundary invariant."""

    def __init__(self) -> None:
        self.received: list[PlaceholderedPayload] = []

    def infer(self, payload: PlaceholderedPayload, max_tokens: int = 512) -> str:
        self.received.append(payload)
        return f"Summary: {payload.text}"
