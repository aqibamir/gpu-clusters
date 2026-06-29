from cmndr.backends.base import Backend, Completion
from cmndr.backends.fake import EchoBackend


def test_echo_backend_returns_completion():
    backend: Backend = EchoBackend()
    out = backend.infer("hello world", max_tokens=10)
    assert isinstance(out, Completion)
    assert "hello world" in out.text
    assert out.tokens_generated == 2
