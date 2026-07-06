from pathlib import Path

import pytest

from cmndr.backends.llamacpp import (DEFAULT_MODEL_PATH, LlamaCppBackend,
                                     llamacpp_available)

needs_llama = pytest.mark.skipif(
    not (llamacpp_available() and Path(DEFAULT_MODEL_PATH).exists()),
    reason="llama-cpp-python or GGUF model not available",
)


def test_available_helper_returns_bool():
    assert isinstance(llamacpp_available(), bool)


@needs_llama
def test_real_inference_returns_completion():
    backend = LlamaCppBackend(DEFAULT_MODEL_PATH)
    out = backend.infer("Answer with one word: what color is the sky?", max_tokens=8)
    assert out.text.strip()
    assert out.tokens_generated > 0
