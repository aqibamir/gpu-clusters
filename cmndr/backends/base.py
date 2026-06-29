from dataclasses import dataclass
from typing import Protocol


@dataclass
class Completion:
    text: str
    tokens_generated: int


class Backend(Protocol):
    """Runs local inference for `local`-routed requests. No backend-specific
    type leaks upward (TR-9)."""

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion: ...
