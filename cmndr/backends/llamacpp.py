"""M6: Apple Silicon local backend via llama.cpp/Metal. It is *a* backend,
not *the* backend (TR-9) — nothing Apple-specific leaks through Completion."""

from functools import lru_cache

from cmndr.backends.base import Completion

DEFAULT_MODEL_PATH = "models/mistral-7b-instruct-v0.2.Q4_K_M.gguf"


def llamacpp_available() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _load(model_path: str, n_ctx: int):
    from llama_cpp import Llama
    return Llama(model_path=model_path, n_ctx=n_ctx, verbose=False)


class LlamaCppBackend:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH, n_ctx: int = 4096) -> None:
        self._model_path = model_path
        self._n_ctx = n_ctx

    def infer(self, prompt: str, max_tokens: int = 512) -> Completion:
        llm = _load(self._model_path, self._n_ctx)
        out = llm.create_completion(
            prompt=f"[INST] {prompt} [/INST]", max_tokens=max_tokens, temperature=0.2)
        text = out["choices"][0]["text"]
        return Completion(text=text, tokens_generated=out["usage"]["completion_tokens"])
