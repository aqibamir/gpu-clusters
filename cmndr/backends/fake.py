from cmndr.backends.base import Completion


class EchoBackend:
    """Dev/test backend. Echoes the prompt as a stand-in for local inference."""

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion:
        return Completion(text=f"[local] {prompt}", tokens_generated=len(prompt.split()))
